"""Workspace page: an application shell with a navigation drawer, a book list,
and a detail pane.

Built from Quasar/NiceGUI structural components (header, drawer, cards, lists)
rather than bare containers, so it reads as an application: elevated header,
bordered drawer, carded panels with separators and list items. Follows the
project polish rules: real icons (no emoji), a loading state on every async
action, no dead controls, and a consistent spacing scale.
"""

from __future__ import annotations

import asyncio
import logging
import re
from pathlib import Path

from nicegui import app, ui

from colophon.controller import AppController
from colophon.core.chapters import file_boundary_chapters
from colophon.core.fields import EDITABLE_FIELDS, field_provenance, get_field
from colophon.core.filename_parser import compile_template
from colophon.core.graph_resolve import _name_key
from colophon.core.models import BookState, BookUnit, FindingSeverity, Phase, PhaseState
from colophon.core.normalize import FIELD_NORMALIZERS, NORMALIZABLE_FIELDS
from colophon.core.perf import span
from colophon.core.review import review_reasons
from colophon.core.tokens import PARSE_TOKENS, parse_field_for
from colophon.core.triage import FACET_DEFAULTS, apply_facets, needs_human, sort_books
from colophon.core.view_state import snapshot_to_view, view_to_snapshot
from colophon.services.ingest import auto_scan_needs_confirmation
from colophon.ui import state_panel
from colophon.ui.chrome import jobs_indicator
from colophon.ui.dialogs import (
    attach_history_menu,
    bulk_remap_dialog,
    bulk_tag_dialog,
    chapter_edit_dialog,
    compare_dialog,
    cover_dialog,
    match_dialog,
    modal,
    persist_dialog,
    quick_match_dialog,
    remap_dialog,
    rename_dialog,
    scan_dialog,
    tag_dialog,
)
from colophon.ui.filter_input import filter_input
from colophon.ui.state_panel import _PHASE_ICONS, _PHASE_LABELS
from colophon.ui.tabs import app_tabs
from colophon.ui.theme import apply_theme, dark_mode_button, setup_dark_mode

logger = logging.getLogger(__name__)

_auto_scan_attempted = False  # once-per-process guard for the lazy scan-if-empty auto-scan

# Sentinel marking a bulk-edit field whose selected books hold differing values.
_MIXED = object()

_TOKEN_RE = re.compile(r"\$(\w+)")


def _placeholder_fields(template: str) -> list[str]:
    """Parse field names a $Token template can produce, in order, no dupes or $Skip."""
    seen: list[str] = []
    for name in _TOKEN_RE.findall(template):
        field = parse_field_for(name)
        if field and field not in seen:
            seen.append(field)
    return seen


def _move_focus(ids: list[str], current: str | None, delta: int) -> str | None:
    """The next focused id when moving by `delta` (+1 down, -1 up) through `ids`.

    From no focus (or a stale id no longer present), Down lands on the first row
    and Up on the last. Movement clamps at the ends (no wrap). Returns None only
    when there is nothing to focus."""
    if not ids:
        return None
    if current is None or current not in ids:
        return ids[0] if delta > 0 else ids[-1]
    index = max(0, min(len(ids) - 1, ids.index(current) + delta))
    return ids[index]


# Short state label + Quasar color for the per-row state badge.
_PAGE = 100  # book rows rendered per chunk (windowed list)

_STATE_BADGE: dict[BookState, tuple[str, str]] = {
    BookState.DETECTED: ("Detected", "grey-6"),
    BookState.IDENTIFIED: ("Identified", "grey-6"),
    BookState.NEEDS_REVIEW: ("Review", "warning"),
    BookState.READY: ("Ready", "positive"),
    BookState.ENCODING: ("Encoding", "info"),
    BookState.ENCODED: ("Encoded", "info"),
    BookState.ORGANIZED: ("Organized", "info"),
    BookState.FAILED: ("Failed", "negative"),
    BookState.SKIPPED: ("Skipped", "grey-6"),
}

_SEVERITY_BADGE: dict[FindingSeverity, tuple[str, str]] = {
    FindingSeverity.ERROR: ("error", "negative"),
    FindingSeverity.WARN: ("warning", "warning"),
    FindingSeverity.INFO: ("info", "info"),
}

# Status-bar state badges: (BookState value, short label, color). Shown only when count > 0.
_STATUS_BADGES = [
    ("detected", "Detected", "grey-6"),
    ("identified", "Identified", "grey-7"),
    ("needs_review", "Needs review", "warning"),
    ("ready", "Ready", "positive"),
    ("organized", "Organized", "info"),
    ("failed", "Failed", "negative"),
]


def _fmt_duration(seconds: float) -> str:
    """Format a file length as hours and minutes, e.g. '1h 2m' or '47m'."""
    minutes = round(seconds / 60)
    hours, mins = divmod(minutes, 60)
    return f"{hours}h {mins}m" if hours else f"{mins}m"



def _confidence_color(value: float) -> str:
    if value >= 75:
        return "positive"
    if value >= 40:
        return "warning"
    return "negative"


def _confidence_tooltip(book: BookUnit, threshold: float) -> str:
    if book.manually_confirmed:
        return "Manually confirmed (100%). Use Recheck Confidence to recompute from sources."
    return (
        f"Match confidence 0-100: how strongly the metadata agrees with the sources. "
        f"At or above {threshold:.0f} a book is auto-marked Ready."
    )


_IDENTITY_TOOLTIP = (
    "Identification confidence 0-100: how sure we are we've correctly identified this book from "
    "your library's structure and file tags, before matching an online source."
)


def _primary_confidence(book: BookUnit, threshold: float) -> tuple[float, str, str]:
    """The confidence to feature for a book: once a source match (or manual confirm) exists, the
    match-verification score; otherwise the pre-match local-identification confidence. `confidence`
    is only ever nonzero post-match, so its presence is what distinguishes the two. Returns
    (value, badge color, tooltip)."""
    if book.confidence > 0:
        return (book.confidence, _confidence_color(book.confidence),
                _confidence_tooltip(book, threshold))
    return (book.identity_confidence, _confidence_color(book.identity_confidence), _IDENTITY_TOOLTIP)


# States that are finished/verified — a review-reason tooltip on their badge would be noise.
_DONE_BADGE_STATES = frozenset({BookState.READY, BookState.ORGANIZED, BookState.ENCODED})


def _review_tooltip(book: BookUnit) -> str | None:
    """A one-line 'why this needs review' for the state badge, or None when nothing is wrong."""
    if book.state in _DONE_BADGE_STATES:
        return None
    reasons = review_reasons(book)
    return " ".join(reasons) if reasons else None


def _state_badge(book: BookUnit) -> None:
    """Render the per-book state badge, with a review-reason tooltip when the book is uncertain."""
    label, color = _STATE_BADGE.get(book.state, (book.state.value, "grey-6"))
    badge = ui.badge(label).props(f"color={color} outline")
    tip = _review_tooltip(book)
    if tip:
        badge.tooltip(tip)


_CHIP_FIELDS = ("genre", "tag")


def book_haystack(book: BookUnit) -> str:
    """Lowercased searchable text for a book: title, authors, narrators, series,
    genres, tags, publisher, and language. Used by the Books list free-text filter
    (and the Manage tab's 'show books' jump)."""
    return " ".join(
        filter(None, [
            book.title or "",
            "; ".join(book.authors),
            "; ".join(book.narrators),
            "; ".join(s.name for s in book.series),
            "; ".join(book.genres),
            "; ".join(book.tags),
            book.publisher or "",
            book.language or "",
        ])
    ).lower()


def _editor_text(widget) -> str:
    """Read an editor widget's value as a '; '-joined string. Chip selects hold a
    list of values; text inputs hold a plain string."""
    value = widget.value
    if isinstance(value, list):
        return "; ".join(x.strip() for x in value if x and x.strip())
    return value or ""


def _cover_src(book: BookUnit, *, thumb: bool = False) -> str | None:
    """The cover-serving URL for a book, or None when it has no cover. The
    `?v=` cache-buster refreshes the image whenever the book changes; `thumb`
    requests the small downscaled cover for the list/navigator rows."""
    if book.cover_path or book.cover_url:
        size = "&size=thumb" if thumb else ""
        return f"/cover/{book.id}?v={int(book.updated_at.timestamp())}{size}"
    return None


def _render_cover(
    book: BookUnit, *, width: int, height: int, icon: str = "", thumb: bool = False
) -> None:
    """Render a book's cover at its natural aspect ratio: the width is fixed and the
    height follows the image, so portrait (book) and square covers both show
    uncropped. `height` sizes the neutral placeholder box shown when there is no
    cover (kept book-shaped). `thumb` serves the small cover (list/navigator rows)."""
    src = _cover_src(book, thumb=thumb)
    if src:
        ui.image(src).classes("rounded").style(f"width:{width}px;height:auto")
    else:
        with ui.element("div").classes("flex items-center justify-center rounded").style(
            f"width:{width}px;height:{height}px;background:rgba(120,120,128,.15)"
        ):
            ui.icon("menu_book", color="grey-6").classes(icon)


def render_workspace(controller: AppController, initial_filter: str = "") -> None:
    apply_theme()
    # Make the content area fill exactly between the fixed header and footer so the
    # three-pane workspace never spills into a page-level scroll (each pane scrolls
    # internally). Flex-fill the Quasar page instead of a fragile fixed height.
    ui.query(".nicegui-content").classes("!p-0").style("flex: 1; min-height: 0")
    ui.query(".q-page").classes("column")
    # Draggable dividers between the three panes. The nav and middle widths are
    # driven by CSS custom properties on :root so NiceGUI's Vue style bindings never
    # clobber them (direct element-style mutation does not survive a re-render). The
    # detail pane keeps flex:1 and absorbs the rest. Widths persist per browser in
    # localStorage; window.colophonResetColumns() (Reset columns button) clears them.
    ui.add_head_html(
        "<style>"
        ".colophon-pane-nav{flex:0 0 var(--colophon-nav-w,260px);"
        "width:var(--colophon-nav-w,260px);min-width:0}"
        ".colophon-pane-mid{flex:0 0 var(--colophon-mid-w,460px);"
        "width:var(--colophon-mid-w,460px);min-width:0}"
        ".colophon-resizer{flex:0 0 9px;cursor:col-resize;align-self:stretch;"
        "display:flex;align-items:center;justify-content:center;touch-action:none}"
        ".colophon-resizer::after{content:'';width:1px;height:32px;border-radius:1px;"
        "background:rgba(120,120,128,.35);transition:background .15s}"
        ".colophon-resizer:hover::after{width:2px}"
        ".colophon-resizer:hover::after{background:var(--q-primary)}"
        "</style>"
    )
    # Run via run_javascript (not a <script> in add_head_html): on the async index
    # page the head HTML is injected after load, and browsers do not execute script
    # tags inserted that way. The IIFE guards against re-running on reconnect.
    ui.run_javascript(
        "(function(){"
        "if(window.__colophonResizerReady)return;window.__colophonResizerReady=true;"
        "var MIN={nav:200,mid:340},MAX={nav:460,mid:600},DET_MIN=460,GAP=18;"
        "var VAR={nav:'--colophon-nav-w',mid:'--colophon-mid-w'},DEF={nav:260,mid:460};"
        "var KEY={nav:'colophon.navW',mid:'colophon.midW'};"
        "function setW(key,px){document.documentElement.style.setProperty(VAR[key],px+'px');}"
        "function getW(k){var v=parseInt(getComputedStyle(document.documentElement)"
        ".getPropertyValue(VAR[k]),10);return isNaN(v)?DEF[k]:v;}"
        # Clamp both panes to their range, then to a window budget so the detail pane never
        # drops below DET_MIN (shrink the middle first, then the nav). Runs on load + resize,
        # so an over-wide persisted width is reined in on a smaller screen instead of squashing.
        "function fit(){var W=window.innerWidth;"
        "var nav=Math.max(MIN.nav,Math.min(MAX.nav,getW('nav')));"
        "var mid=Math.max(MIN.mid,Math.min(MAX.mid,getW('mid')));"
        "var over=(nav+mid+GAP+DET_MIN)-W;"
        "if(over>0){var m=Math.max(MIN.mid,mid-over);over-=(mid-m);mid=m;}"
        "if(over>0){nav=Math.max(MIN.nav,nav-over);}"
        "setW('nav',nav);setW('mid',mid);}"
        "['nav','mid'].forEach(function(k){"
        "var v=localStorage.getItem(KEY[k]);if(v)setW(k,parseInt(v,10));});"
        "fit();window.addEventListener('resize',fit);"
        "var drag=null;"
        "document.addEventListener('pointerdown',function(e){"
        "var h=e.target.closest&&e.target.closest('.colophon-resizer');if(!h)return;"
        "var pane=h.previousElementSibling;if(!pane)return;"
        "var key=pane.classList.contains('colophon-pane-nav')?'nav':'mid';"
        "drag={key:key,startX:e.clientX,startW:pane.getBoundingClientRect().width};"
        "document.body.style.userSelect='none';e.preventDefault();});"
        "document.addEventListener('pointermove',function(e){"
        "if(!drag)return;var w=drag.startW+(e.clientX-drag.startX);"
        "w=Math.round(Math.max(MIN[drag.key],Math.min(MAX[drag.key],w)));"
        "setW(drag.key,w);fit();drag.last=getW(drag.key);});"
        "document.addEventListener('pointerup',function(){"
        "if(!drag)return;if(drag.last)localStorage.setItem(KEY[drag.key],drag.last);"
        "drag=null;document.body.style.userSelect='';});"
        "window.colophonResetColumns=function(){"
        "['nav','mid'].forEach(function(k){"
        "localStorage.removeItem(KEY[k]);"
        "document.documentElement.style.removeProperty(VAR[k]);});fit();};"
        "})();"
    )
    dark = setup_dark_mode()
    selected_ids: set[str] = set()
    # `scope` is the author/series/all/needs_id selection; `folder_filter` is an
    # orthogonal, persistent constraint set by browsing a folder. Both the Books
    # list and the navigator (author/series list) respect the folder filter, and
    # a scope selection refines within it.
    scope: dict[str, object] = {"kind": "all", "key": None}
    folder_filter: dict[str, object] = {"path": None}
    book_filter: dict[str, str] = {"text": initial_filter or ""}
    editor_state: dict[str, object] = {
        "book_id": None, "is_dirty": None, "save_pending": None, "save": None, "write": None,
    }

    def _set_dirty(value: bool) -> None:
        """Mirror the editor's unsaved-changes state into the browser so the
        beforeunload guard (see the keyboard/unload block) can warn on navigation."""
        ui.run_javascript(f"window.__colophon_dirty = {'true' if value else 'false'}")

    def _clear_editor_state() -> None:
        editor_state.update(
            book_id=None, is_dirty=None, save_pending=None, save=None, write=None
        )
        _set_dirty(False)
        _persist_view()

    def _guard_nav(target_book_id, then) -> bool:
        """If a different dirty detail is open, prompt and return True (handled);
        else return False so the caller proceeds."""
        is_dirty = editor_state["is_dirty"]
        if is_dirty is None or not is_dirty() or editor_state["book_id"] == target_book_id:
            return False
        with modal() as d, ui.card().classes("w-96"):
            ui.label("Unsaved changes").classes("text-subtitle1")
            ui.label("This book has unsaved edits.").classes("text-caption colophon-muted")
            with ui.row().classes("w-full justify-end q-gutter-sm q-mt-sm"):
                ui.button("Cancel", on_click=d.close).props("flat")
                ui.button(
                    "Discard", on_click=lambda: (d.close(), _clear_editor_state(), then())
                ).props("flat color=negative")

                def _save_and() -> None:
                    save_pending = editor_state["save_pending"]
                    if save_pending is not None:
                        save_pending()
                    d.close()
                    then()

                ui.button("Save and continue", icon="save", on_click=_save_and)
        d.open()
        return True

    # Keyboard navigation: the focused book row and the live row elements + a
    # registry of widgets the shortcuts drive (the filter input).
    focus: dict[str, str | None] = {"id": None}
    row_elements: dict[str, ui.item] = {}
    refs: dict[str, object] = {"filter": None}
    view: dict[str, object] = {
        "multiselect": False, "group_by": "author",
    }
    _list_view: dict[str, object] = {"books": [], "rendered": 0}
    _list_el: dict[str, object] = {"el": None}      # the ui.list() holding rows
    _list_footer: dict[str, object] = {"el": None}  # the "Showing X of Y" caption
    list_scroll = None                              # assigned at the layout site

    _VIEW_KEY = "workspace_view"

    def _tab_storage():
        """app.storage.tab, or None when the client is not connected (e.g. a
        reconnect race or a background callback). View persistence is best-effort:
        a transient no-connection must never raise and 500 the page."""
        try:
            return app.storage.tab
        except RuntimeError:
            return None

    def _persist_view() -> None:
        store = _tab_storage()
        if store is None:
            return
        store[_VIEW_KEY] = view_to_snapshot(
            scope=scope, folder_filter=folder_filter, view=view,
            filter_text=book_filter["text"], selected_ids=selected_ids,
            open_book_id=editor_state["book_id"],
        )

    _tab = _tab_storage()
    _restored = snapshot_to_view(
        _tab.get(_VIEW_KEY) if _tab is not None else None,
        known_book_ids={b.id for b in controller.books_all()},
        known_authors=set(controller.known_authors()),
        known_series=set(controller.known_series()),
    )
    scope.update(_restored.scope)
    folder_filter["path"] = _restored.folder_filter_path
    view.update(_restored.view)
    if not initial_filter:  # an explicit ?filter= query wins over the snapshot
        book_filter["text"] = _restored.filter_text
    selected_ids.update(_restored.selected_ids)

    # Triage view-state is ephemeral — always default to Triage on open (not persisted).
    view["mode"] = "triage"
    view["facets"] = dict(FACET_DEFAULTS)
    view["sort"] = "conf_asc"

    def _selected_books() -> list:
        return [b for b in (controller.get_book(i) for i in selected_ids) if b is not None]

    def _in_folder(book) -> bool:
        """True when `book` is within the active folder filter (or none is set)."""
        path = folder_filter["path"]
        if not path:
            return True
        folder = Path(str(path))
        return book.source_folder == folder or folder in book.source_folder.parents

    def _books_for_scope() -> list:
        tree = controller.library_tree()
        kind, key = scope["kind"], scope["key"]
        if kind == "attention":
            return controller.books_needing_attention()
        if kind == "needs_id":
            books = list(tree.needs_id)
        elif kind == "author":
            node = next((a for a in tree.authors if a.name == key), None)
            # dedup by id: a book in two of this author's series is filed under each
            books = list({b.id: b for s in node.series for b in s.books}.values()) + node.standalone if node else []
        elif kind == "series" and key:
            books = [b for s in tree.series if s.name == key for b in s.books]
        elif kind == "franchise" and key:
            books = [b for f in tree.franchises if f.name == key for b in f.books]
        elif kind == "phase" and key:
            books = controller.books_with_phase(Phase(key), PhaseState.FRESH)
        else:  # "all"
            books = list(tree.all_books)
        # The folder filter applies on top of every scope selection.
        return [b for b in books if _in_folder(b)]

    def _matches_filter(book, terms: list[str]) -> bool:
        if not terms:
            return True
        hay = f"{book_haystack(book)} {controller.book_filename(book).lower()}"
        return all(term in hay for term in terms)

    def _visible_books() -> list:
        """Scope ∧ folder ∧ text, then (Triage) the needs-a-human filter, the active facets,
        and the chosen sort. Triage opens worst-confidence-first on the books needing attention."""
        books = _books_for_scope()
        terms = book_filter["text"].lower().split()
        if terms:
            books = [b for b in books if _matches_filter(b, terms)]
        if view["mode"] == "triage":
            books = [b for b in books if needs_human(b)]
        books = apply_facets(books, view["facets"])
        return sort_books(books, view["sort"])

    # --- attention pane (findings + guided actions) ---
    def render_attention_pane(book) -> None:
        findings = controller._active_findings(book)
        with ui.column().classes("w-full gap-2"):
            for f in findings:
                icon, color = _SEVERITY_BADGE[f.severity]
                with ui.row().classes("items-center gap-2"):
                    ui.icon(icon).props(f"color={color}")
                    ui.label(f.detail)
            for f in findings:
                if f.code.value in ("dup_format", "dup_edition", "structure_unclear"):
                    code = f.code

                    def _ack(c=code, b=book) -> None:
                        controller.acknowledge_finding(b, c)
                        ui.notify("Acknowledged", type="info")
                        refresh_nav()
                        _render_middle()

                    ui.button("Acknowledge", on_click=_ack).props("flat color=primary")

    # --- detail pane ---
    def show_detail(book_id: str) -> None:
        if _guard_nav(book_id, lambda: show_detail(book_id)):
            return
        detail_container.clear()
        book = controller.get_book(book_id)
        with detail_container:
            if book is None:
                _clear_editor_state()
                with ui.column().classes("w-full items-center q-pa-lg"):
                    ui.icon("menu_book").classes("text-h3 text-grey-5")
                    ui.label("Select a book to see its details").classes("colophon-muted")
                return

            def _details_body() -> None:
                # editable fields, each prefilled with its value + provenance badge
                inputs: dict[str, ui.input | ui.textarea] = {}
                originals: dict[str, str] = {}
                autocomplete = {"author": controller.known_authors(), "series": controller.known_series()}

                def _build_field(field: str) -> None:
                    value = get_field(book, field) or ""
                    originals[field] = value
                    with ui.row().classes("items-center w-full no-wrap q-gutter-xs"):
                        if field in _CHIP_FIELDS:
                            current = [s.strip() for s in value.split(";") if s.strip()]
                            known = controller.known_genres() if field == "genre" else controller.known_tags()
                            if field == "genre":
                                known = sorted(set(known) | set(controller.genre_policy().accepted))
                            inp = ui.select(
                                sorted(set(known) | set(current)), label=field, value=current,
                                multiple=True, new_value_mode="add-unique",
                            ).props("use-chips use-input dense").classes("col")
                            inputs[field] = inp
                            source = field_provenance(book, field)
                            if source:
                                ui.badge(controller.source_label(source)).props("outline").classes("colophon-chip").classes("self-center").tooltip(controller.source_tooltip(source))
                            return
                        if field == "description":
                            inp = ui.textarea(field, value=value).props("dense").classes("col")
                        else:
                            inp = ui.input(
                                field, value=value, autocomplete=autocomplete.get(field)
                            ).props("dense").classes("col")
                            if field in ("year", "asin", "isbn"):
                                inp.classes("colophon-mono")
                        inputs[field] = inp
                        source = field_provenance(book, field)
                        if source:
                            ui.badge(controller.source_label(source)).props("outline").classes("colophon-chip").classes("self-center").tooltip(controller.source_tooltip(source))

                def _save_pending(b=book) -> bool:
                    """Persist any pending field edits silently, advancing the editor's
                    baseline. Returns True when something was saved."""
                    changed = {
                        f: (_editor_text(inputs[f]) or None)
                        for f in EDITABLE_FIELDS
                        if _editor_text(inputs[f]) != originals[f]
                    }
                    if not changed:
                        return False
                    controller.save_fields(b, changed)
                    for f in changed:
                        originals[f] = _editor_text(inputs[f])
                    return True

                def _is_dirty() -> bool:
                    return any(_editor_text(inputs[f]) != originals[f] for f in EDITABLE_FIELDS)

                def _save(b=book) -> None:
                    if not _save_pending(b):
                        ui.notify("No changes")
                        return
                    _set_dirty(False)
                    ui.notify("Saved")
                    refresh_list()
                    show_detail(b.id)

                def _normalize_all() -> None:
                    for f, inp in inputs.items():
                        fn = FIELD_NORMALIZERS.get(f)
                        if fn is None or f in _CHIP_FIELDS:
                            continue
                        inp.set_value(fn(_editor_text(inp)))
                    ui.notify("Normalized fields")

                async def _fetch_chapters(b=book, asin=None) -> None:
                    res = await controller.apply_audnexus_chapters(b, asin=asin)
                    if not res.ok:
                        ui.notify(res.error or "No chapters found", type="warning")
                        return
                    ui.notify(f"Applied {res.count} chapters from Audible")
                    if res.mismatch:
                        def _fmt(ms: int) -> str:
                            s = ms // 1000
                            return f"{s // 3600}:{(s % 3600) // 60:02d}"
                        ui.notify(
                            f"Audible runtime {_fmt(res.audible_runtime_ms)} vs your files "
                            f"{_fmt(res.source_runtime_ms)} - chapters may not line up",
                            type="warning", timeout=8000,
                        )
                    show_detail(b.id)

                async def _fetch_clicked(b=book) -> None:
                    if (b.asin or "").strip():
                        await _fetch_chapters(b)
                        return
                    with modal() as dlg, ui.card().classes("w-80"):
                        ui.label("Fetch chapters from Audible").classes("text-subtitle1")
                        asin_in = ui.input("ASIN").props("dense").classes("w-full")
                        with ui.row().classes("w-full justify-end q-gutter-sm q-mt-sm"):
                            ui.button("Cancel", on_click=dlg.close).props("flat")

                            async def _go() -> None:
                                value = (asin_in.value or "").strip()
                                if not value:
                                    ui.notify("Enter an ASIN")
                                    return
                                dlg.close()
                                await _fetch_chapters(b, asin=value)

                            ui.button("Fetch", icon="cloud_download", on_click=_go)
                    dlg.open()

                with ui.row().classes("w-full no-wrap items-start q-gutter-md"):
                    # Left aside: cover, status, location.
                    with ui.column().classes("items-center q-gutter-xs").style("width: 120px; flex: 0 0 120px"):
                        _render_cover(book, width=112, height=168, icon="text-h2")
                        _cval, _ccolor, _ctip = _primary_confidence(book, controller.review_threshold())
                        ui.badge(f"{_cval:.0f}").props(f"color={_ccolor}").tooltip(_ctip)
                        _state_badge(book)
                    # Main column: title, source path, tools, grouped fields.
                    with ui.column().classes("col q-gutter-none").style("min-width: 0"):
                        ui.label(book.title or "(untitled)").classes("colophon-book-title text-h6")
                        if book.source_folder is not None:
                            with ui.row().classes("items-center no-wrap w-full q-gutter-xs q-mb-xs"):
                                ui.icon("folder", size="14px").classes("colophon-muted")
                                ui.label(str(book.source_folder)).classes(
                                    "text-caption colophon-muted ellipsis col"
                                ).style("min-width: 0; direction: rtl; text-align: left; "
                                        "unicode-bidi: plaintext").tooltip(str(book.source_folder))
                        if book.confidence_signals:
                            with ui.row().classes("items-center w-full q-gutter-xs q-mb-xs"):
                                for sig in book.confidence_signals:
                                    color = "positive" if sig.points >= 0 else "negative"
                                    ui.badge(f"{sig.name.replace('_', ' ')} {sig.points:+d}").props(
                                        f"color={color} outline"
                                    ).tooltip(sig.detail)

                        # --- metadata tool groups ---
                        with ui.row().classes("w-full no-wrap q-gutter-sm q-mb-sm"):
                            with ui.element("div").classes("colophon-toolgroup col"):
                                ui.label("Fetch from sources").classes("colophon-seccap")
                                with ui.row().classes("q-gutter-xs"):
                                    ui.button("Matches", icon="travel_explore", on_click=lambda b=book: compare_dialog(controller, b, show_detail=show_detail, refresh_list=refresh_list)).props("flat dense no-caps").tooltip("Find and apply metadata matches")
                                    ui.button("Chapters", icon="toc", on_click=_fetch_clicked).props("flat dense no-caps").tooltip("Fetch chapters from Audible")
                                    ui.button("Cover", icon="image", on_click=lambda b=book: cover_dialog(controller, b, show_detail=show_detail)).props("flat dense no-caps").tooltip("Search or set the cover")
                            with ui.element("div").classes("colophon-toolgroup"):
                                ui.label("Confidence").classes("colophon-seccap")
                                with ui.row().classes("q-gutter-xs"):
                                    if book.manually_confirmed:
                                        async def _recheck(b=book) -> None:
                                            ui.notify("Rechecking confidence...")
                                            await controller.recheck_confidence(b)
                                            show_detail(b.id)
                                            refresh_list()
                                            refresh_status()
                                        ui.button("Recheck Confidence", icon="refresh", on_click=_recheck).props(
                                            "flat dense no-caps"
                                        ).tooltip("Re-query sources and revert to the computed confidence")
                                    else:
                                        def _confirm(b=book) -> None:
                                            controller.confirm_confidence(b)
                                            show_detail(b.id)
                                            refresh_list()
                                            refresh_status()
                                        ui.button("Manual Confirmation", icon="verified", on_click=_confirm).props(
                                            "flat dense no-caps"
                                        ).tooltip("Confirm this book and set its confidence to 100%")
                            with ui.element("div").classes("colophon-toolgroup"):
                                ui.label("Clean up").classes("colophon-seccap")
                                with ui.row().classes("q-gutter-xs"):
                                    ui.button("Normalize", icon="text_format", on_click=_normalize_all).props("flat dense no-caps").tooltip("Normalize all text fields")
                                    ui.button("Remap", icon="swap_horiz", on_click=lambda b=book: remap_dialog(controller, b, refresh_list=refresh_list, show_detail=show_detail)).props("flat dense no-caps").tooltip("Move one field's value to another")
                            if book.missing:
                                with ui.element("div").classes("colophon-toolgroup"):
                                    ui.label("Missing").classes("colophon-seccap")
                                    with ui.row().classes("q-gutter-xs"):
                                        def _remove_missing(b=book) -> None:
                                            controller.remove_missing(b)
                                            ui.notify("Removed missing book")
                                            # The record is gone; show_detail(None-ish)
                                            # renders the empty placeholder and clears
                                            # editor state for the now-deleted id.
                                            show_detail(b.id)
                                            refresh_list()
                                            refresh_status()
                                        ui.button("Remove missing", icon="delete_outline", on_click=_remove_missing).props(
                                            "flat dense no-caps color=negative"
                                        ).tooltip("Delete this orphaned record (the folder is gone)")

                        # --- grouped fields ---
                        ui.label("Identity").classes("colophon-seccap")
                        with ui.grid(columns=2).classes("w-full"):
                            for f in ("title", "subtitle", "author", "narrator", "series", "sequence"):
                                _build_field(f)
                        ui.label("Description").classes("colophon-seccap")
                        _build_field("description")
                        ui.label("Publication").classes("colophon-seccap")
                        with ui.grid(columns=2).classes("w-full"):
                            for f in ("year", "publisher", "language", "asin", "isbn"):
                                _build_field(f)
                        _abridged_opts = {None: "Unknown", False: "Unabridged", True: "Abridged"}
                        ui.select(
                            _abridged_opts, value=book.abridged, label="Abridged",
                            on_change=lambda e, b=book: (controller.set_abridged(b, e.value), refresh_list()),
                        ).props("dense").classes("w-full")
                        ui.label("Classification").classes("colophon-seccap")
                        _build_field("genre")
                        _build_field("tag")

                for _inp in inputs.values():
                    _inp.on_value_change(lambda _e=None: _set_dirty(True))

                with ui.row().classes("colophon-actionbar w-full no-wrap items-center q-gutter-sm"):
                    ui.button("Save", icon="save", on_click=_save).props("unelevated")
                    ui.button("Write tags", icon="sell", on_click=lambda b=book: tag_dialog(controller, b, refresh_list=refresh_list, refresh_status=refresh_status, save_pending=lambda: _save_pending(b))).props("outline")
                    ui.space()
                    ui.button(
                        "Mark ready", icon="check",
                        on_click=lambda b=book: (controller.mark_ready(b), ui.notify("Marked ready"), refresh_list()),
                    ).props("flat")

                editor_state.update(
                    book_id=book_id, is_dirty=_is_dirty,
                    save_pending=_save_pending, save=_save,
                    write=lambda b=book: tag_dialog(controller, b, refresh_list=refresh_list, refresh_status=refresh_status, save_pending=lambda: _save_pending(b)),
                )
                _set_dirty(False)
                _persist_view()

                if controller._active_findings(book):
                    ui.separator().classes("q-my-sm")
                    ui.label("Attention").classes("text-subtitle2")
                    render_attention_pane(book)

                if book.source_files:
                    ui.separator().classes("q-my-sm")
                    ui.label(f"Files ({len(book.source_files)})").classes("text-subtitle2")

                    with ui.list().props("dense bordered").classes("w-full"):
                        for idx, sf in enumerate(book.source_files):
                            with ui.item():
                                with ui.item_section():
                                    ui.item_label(sf.path.name)
                                    ui.item_label(_fmt_duration(sf.duration_seconds)).props("caption")
                                with ui.item_section().props("side"):
                                    with ui.row().classes("q-gutter-xs no-wrap"):
                                        ui.button(icon="arrow_upward", on_click=lambda p=sf.path: (controller.move_file(book, p, -1), show_detail(book.id))).props('flat dense round aria-label="Move file up"').set_enabled(idx > 0)
                                        ui.button(icon="arrow_downward", on_click=lambda p=sf.path: (controller.move_file(book, p, 1), show_detail(book.id))).props('flat dense round aria-label="Move file down"').set_enabled(idx < len(book.source_files) - 1)
                                        ui.button(icon="edit", on_click=lambda p=sf.path: rename_dialog(controller, book, p, show_detail=show_detail)).props('flat dense round aria-label="Rename file"')
                                        ui.button(icon="remove_circle_outline", on_click=lambda p=sf.path: (controller.exclude_file(book, p), ui.notify("Excluded"), show_detail(book.id))).props('flat dense round color=negative aria-label="Exclude file"')

                    # chapters: applied named chapters (book.chapters) or file-boundary default
                    applied = bool(book.chapters)
                    chapters = book.chapters if applied else file_boundary_chapters(
                        [(sf.path.name, sf.duration_seconds) for sf in book.source_files]
                    )
                    with ui.row().classes("items-center w-full no-wrap q-mt-sm"):
                        ui.label(f"Chapters ({len(chapters)})").classes("text-subtitle2")
                        if applied:
                            ui.badge("custom").props("outline").classes("colophon-chip").classes("self-center")
                        ui.space()
                        ui.button(
                            "Edit", icon="edit",
                            on_click=lambda b=book, chs=chapters: chapter_edit_dialog(
                                controller, b, chs, show_detail=show_detail
                            ),
                        ).props("flat dense no-caps")
                        ui.button(
                            "Fetch from Audible", icon="cloud_download", on_click=_fetch_clicked
                        ).props("flat dense no-caps")
                        if applied:
                            ui.button(
                                "Reset to file boundaries", icon="restart_alt",
                                on_click=lambda b=book: (controller.reset_chapters(b), show_detail(b.id)),
                            ).props("flat dense no-caps")
                    with ui.list().props("dense").classes("w-full"):
                        for n, ch in enumerate(chapters, start=1):
                            with ui.item():
                                with ui.item_section():
                                    ui.item_label(f"{n}. {ch.title}")
                                with ui.item_section().props("side"):
                                    _t = ch.start_ms // 1000
                                    ui.item_label(
                                        f"{_t // 3600}:{(_t % 3600) // 60:02d}:{_t % 60:02d}"
                                    ).props("caption")

            with ui.tabs().props("dense no-caps").classes("w-full") as _tabs:
                ui.tab("details", label="Details", icon="edit")
                ui.tab("state", label="State", icon="insights")
            with ui.tab_panels(_tabs, value="details").classes("w-full"):
                with ui.tab_panel("details").classes("q-pa-none"):
                    _details_body()
                with ui.tab_panel("state").classes("q-pa-none"):
                    state_panel.render(controller, book)

    def _clear_selection() -> None:
        """Clear the ENTIRE selection across every view and collapse the bulk
        panel. The single canonical 'clear everything' used by the footer button,
        the bulk Clear selection button, the navigator Deselect all, and after a
        bulk operation runs on the selection."""
        selected_ids.clear()
        refresh_nav()
        refresh_list()
        refresh_status()
        _update_count()
        show_detail("")

    # --- bulk editor (shown when 2+ books are selected) ---
    def show_bulk() -> None:
        if _guard_nav(None, show_bulk):
            return
        _clear_editor_state()
        detail_container.clear()
        books = _selected_books()
        with detail_container:
            with ui.row().classes("items-center w-full"):
                ui.icon("edit_note").classes("text-h6")
                ui.label(f"Editing {len(books)} books").classes("text-h6 q-ml-xs")
            ui.label("Blank fields are left unchanged.").classes("text-caption colophon-muted")
            ui.separator().classes("q-my-sm")

            inputs: dict[str, ui.input | ui.textarea] = {}
            originals: dict[str, object] = {}
            autocomplete = {"author": controller.known_authors(), "series": controller.known_series()}
            for field in EDITABLE_FIELDS:
                values = {(get_field(b, field) or "") for b in books}
                mixed = len(values) > 1
                common = "" if mixed else next(iter(values), "")
                originals[field] = _MIXED if mixed else common
                with ui.row().classes("items-center w-full no-wrap q-gutter-xs"):
                    if field in _CHIP_FIELDS:
                        current = [s.strip() for s in common.split(";") if s.strip()]
                        known = controller.known_genres() if field == "genre" else controller.known_tags()
                        if field == "genre":
                            known = sorted(set(known) | set(controller.genre_policy().accepted))
                        inp = ui.select(
                            sorted(set(known) | set(current)), label=field,
                            value=[] if mixed else current,
                            multiple=True, new_value_mode="add-unique",
                        ).props("use-chips use-input dense").classes("col")
                        if mixed:
                            inp.props('hint="(multiple values)"')
                        inputs[field] = inp
                        continue
                    if field == "description":
                        inp = ui.textarea(field, value=common).props("dense").classes("col")
                    else:
                        inp = ui.input(
                            field, value=common, autocomplete=autocomplete.get(field)
                        ).props("dense").classes("col")
                    if mixed:
                        inp.props('placeholder="(multiple values)"')
                    inputs[field] = inp

            def _apply_pending_bulk() -> int:
                """Apply pending bulk-field edits silently, advancing the baseline.
                Returns the number of fields applied across the selection."""
                applied = 0
                for field, inp in inputs.items():
                    current = _editor_text(inp)
                    original = originals[field]
                    if original is _MIXED:
                        if not current:  # only touch a mixed field if the user set something
                            continue
                        value: str | None = current
                    elif current != original:
                        value = current or None
                    else:
                        continue
                    controller.bulk_edit(books, field, value)
                    originals[field] = current  # baseline now matches the applied value
                    applied += 1
                return applied

            def _apply_bulk() -> None:
                n = _apply_pending_bulk()
                if not n:
                    ui.notify("No changes")
                    return
                ui.notify(f"Updated {n} field(s) on {len(books)} books")
                _clear_selection()


            ui.separator().classes("q-my-sm")
            with ui.row().classes("items-center w-full no-wrap q-gutter-sm"):
                ui.label("Normalize").classes("text-caption colophon-muted")
                norm_options = {"__all__": "All text fields"} | {f: f for f in NORMALIZABLE_FIELDS}
                norm_field = ui.select(
                    norm_options, value="__all__"
                ).props("dense outlined").classes("col")

                def _normalize() -> None:
                    chosen = norm_field.value
                    fields = NORMALIZABLE_FIELDS if chosen == "__all__" else [chosen]
                    batch = controller.bulk_normalize(books, fields)
                    changed = len({c.book_id for c in controller.batch_changes(batch)})
                    if not changed:
                        ui.notify("Nothing to normalize")
                        return
                    ui.notify(
                        f"Normalized {changed} book(s)",
                        actions=[
                            {
                                "label": "Undo",
                                "color": "white",
                                "handler": lambda b=batch: (controller.undo(b), refresh_list(), refresh_status()),
                            }
                        ],
                    )
                    _clear_selection()

                ui.button("Normalize", icon="text_format", on_click=_normalize).props("outline")


            with ui.row().classes("q-gutter-sm q-mt-sm"):
                ui.button("Quick Match", icon="bolt", on_click=lambda: quick_match_dialog(controller, books, clear_selection=_clear_selection)).props("outline")
                ui.button("Remap", icon="swap_horiz", on_click=lambda: bulk_remap_dialog(controller, books, clear_selection=_clear_selection)).props("outline").tooltip("Move one field's value to another across the selection")

            with ui.row().classes("q-gutter-sm q-mt-sm"):
                ui.button("Apply to selection", icon="done_all", on_click=_apply_bulk)
                ui.button("Write tags", icon="sell", on_click=lambda: bulk_tag_dialog(controller, books, clear_selection=_clear_selection, apply_pending_bulk=_apply_pending_bulk)).props("outline")
                rerun_btn = ui.button("Re-run phase", icon="refresh").props("outline color=grey-6")
                rerun_btn.set_enabled(False)
                rerun_btn.tooltip("Re-run a phase across the selection — coming soon")
                ui.button(
                    "Clear selection", icon="clear", on_click=_clear_selection,
                ).props("flat")

    # --- book list ---
    def _select_all(book_ids: list[str]) -> None:
        # Navigator "Select all": all books in the current scope (ignores filter).
        selected_ids.update(book_ids)
        refresh_nav()
        refresh_list()
        refresh_status()
        _update_count()
        _after_select()
        _persist_view()

    def _deselect_all() -> None:
        # _clear_selection already clears + refreshes nav/list/status/count and
        # collapses the detail pane; we only add view persistence on top.
        _clear_selection()
        _persist_view()

    def _select_visible() -> None:
        # Books-header "Select all": additive over the filtered, visible books.
        selected_ids.update(b.id for b in _visible_books())
        refresh_nav()
        refresh_list()
        refresh_status()
        _update_count()
        _after_select()
        _persist_view()

    def _deselect_visible() -> None:
        # Books-header "Deselect all": subtractive over the filtered, visible books;
        # selections outside the current filter are left untouched.
        selected_ids.difference_update(b.id for b in _visible_books())
        refresh_nav()
        refresh_list()
        refresh_status()
        _update_count()
        _after_select()
        _persist_view()

    def _toggle_book(book_id: str, on: bool) -> None:
        if on:
            selected_ids.add(book_id)
        else:
            selected_ids.discard(book_id)
        refresh_nav()  # keep navigator node checkboxes in sync
        refresh_status()
        _update_count()
        _after_select()
        _persist_view()

    def _build_row(book) -> None:
        # Every book is always individually selectable. The leading checkbox
        # toggles selection; clicking the title section opens the detail view.
        # Rows are keyboard-navigable; the focused row is tinted.
        item = ui.item().classes("book-row")
        row_elements[book.id] = item
        if book.id == focus["id"]:
            item.classes("book-row-focused")
        with item:
            with ui.item_section().props("avatar"):
                ui.checkbox(
                    value=book.id in selected_ids,
                    on_change=lambda e, bid=book.id: _toggle_book(bid, e.value),
                ).props("dense")
            with ui.item_section().props("avatar"):
                _render_cover(book, width=36, height=54, thumb=True)
            with ui.item_section().classes("cursor-pointer").on(
                "click", lambda bid=book.id: _set_focus(bid)
            ):
                # Line 1: title (ellipsized) with confidence + state badges
                # pinned right, so the badges never clip on a narrow pane.
                with ui.row().classes("items-center w-full no-wrap q-gutter-xs"):
                    ui.label(book.title or "(untitled)").classes(
                        "colophon-book-title col ellipsis"
                    )
                    total = sum(sf.duration_seconds for sf in book.source_files)
                    if book.source_files:
                        ui.label(_fmt_duration(total)).classes(
                            "text-caption colophon-muted colophon-mono"
                        )
                    _cval, _ccolor, _ctip = _primary_confidence(book, controller.review_threshold())
                    ui.badge(f"{_cval:.0f}").props(f"color={_ccolor}").tooltip(_ctip)
                    _state_badge(book)
                    if book.missing:
                        ui.badge("Missing").props("color=warning outline").tooltip(
                            "This book's folder is gone from disk. Remove it or "
                            "restore the folder and rescan."
                        )
                series = book.series[0].name if book.series else ""
                author = ", ".join(book.authors) or "unknown author"
                line2 = f"{author} · {series}" if series else author
                ui.item_label(line2).props("caption")
                chip_labels = book.genres + book.tags
                if chip_labels:
                    with ui.row().classes("items-center no-wrap q-gutter-xs q-mt-none"):
                        for label in chip_labels[:4]:
                            ui.chip(label).props(
                                "dense square size=sm clickable"
                            ).on(
                                "click.stop", lambda lbl=label: _filter_to(lbl)
                            )

    def _update_list_footer() -> None:
        el = _list_footer["el"]
        if el is None:
            return
        n, total = _list_view["rendered"], len(_list_view["books"])
        if n < total:
            el.set_text(f"Showing {n} of {total}; scroll for more")
            el.set_visibility(True)
        else:
            el.set_visibility(False)

    def _render_more() -> None:
        books = _list_view["books"]
        start = _list_view["rendered"]
        end = min(start + _PAGE, len(books))
        if start >= end or _list_el["el"] is None:
            return
        with _list_el["el"]:
            for book in books[start:end]:
                _build_row(book)
        _list_view["rendered"] = end
        _update_list_footer()

    def _ensure_rendered(book_id: str) -> None:
        """Render forward until book_id's row exists (focus/open beyond page 1)."""
        ids = [b.id for b in _list_view["books"]]
        if book_id not in ids:
            return
        idx = ids.index(book_id)
        while _list_view["rendered"] <= idx and _list_view["rendered"] < len(_list_view["books"]):
            _render_more()

    def _on_list_scroll(e) -> None:
        if e.vertical_percentage > 0.85 and _list_view["rendered"] < len(_list_view["books"]):
            _render_more()

    def refresh_list() -> None:
        with span("list render"):
            _refresh_list()

    def _refresh_list() -> None:
        list_container.clear()
        row_elements.clear()
        books = _visible_books()
        _list_view["books"] = books
        _list_view["rendered"] = 0
        with list_container:
            if not books:
                msg = (
                    "No books match the filter" if book_filter["text"].strip()
                    else "No books in this view"
                )
                ui.label(msg).classes("colophon-muted q-pa-md")
                _list_el["el"] = None
                _list_footer["el"] = None
                return
            _list_el["el"] = ui.list().props("separator dense").classes("w-full")
            _list_footer["el"] = ui.label().classes("text-caption colophon-muted q-pa-sm")
        _render_more()
        if list_scroll is not None:
            list_scroll.scroll_to(percent=0.0)

    # --- keyboard navigation ---
    def _set_focus(book_id: str) -> None:
        """Focus a book row: tint it, open it in Details, and scroll it into view."""
        _ensure_rendered(book_id)
        old = focus["id"]
        focus["id"] = book_id
        if old in row_elements:
            row_elements[old].classes(remove="book-row-focused")
        if book_id in row_elements:
            row_elements[book_id].classes(add="book-row-focused")
            ui.run_javascript(
                f'getElement({row_elements[book_id].id}).$el.scrollIntoView({{block:"nearest"}})'
            )
        show_detail(book_id)

    def _nav_focus(delta: int) -> None:
        ids = [b.id for b in _visible_books()]
        new = _move_focus(ids, focus["id"], delta)
        if new is not None:
            _set_focus(new)

    def _toggle_focused() -> None:
        book_id = focus["id"]
        if not book_id:
            return
        if book_id in selected_ids:
            selected_ids.discard(book_id)
        else:
            selected_ids.add(book_id)
        refresh_list()  # re-render the checkbox (and re-apply the focus tint)
        refresh_nav()   # keep navigator node checkboxes in sync
        refresh_status()
        _update_count()

    def _on_key(e) -> None:
        # NiceGUI's `ignore` list keeps these keys from firing while a text
        # field/button is focused.
        key, action, mods = e.key, e.action, e.modifiers
        if mods.ctrl or mods.alt or mods.meta:
            return
        # "/" focuses the filter; act on keyup so the slash isn't typed into it.
        if action.keyup and key.name == "/":
            if refs["filter"] is not None:
                refs["filter"].run_method("focus")
            return
        if not action.keydown:
            return
        if key.arrow_down or key.name == "j":
            _nav_focus(1)
        elif key.arrow_up or key.name == "k":
            _nav_focus(-1)
        elif key.space and not action.repeat:
            _toggle_focused()
        elif key.enter and not action.repeat and focus["id"]:
            show_detail(focus["id"])

    # --- parse from filename ---
    def _parse_dialog() -> None:
        books = _selected_books()
        if not books:
            ui.notify("Select one or more books first")
            return
        initial_pattern = controller.ctx.config.filename_template or "$Author - $Title"
        chosen: dict[str, bool] = {}

        with modal() as dialog, ui.card().classes("w-full").style("max-width: 720px"):
            ui.label("Parse from filename").classes("text-h6")
            ui.label(f"Applies to {len(books)} selected book(s).").classes(
                "text-caption colophon-muted"
            )

            # Field key: the $Tokens that can appear in a parse pattern.
            with ui.row().classes("items-center q-gutter-xs q-mt-xs"):
                ui.label("Fields:").classes("text-caption colophon-muted")
                for tok in PARSE_TOKENS:
                    badge = ui.badge(f"${tok.name}").props("color=grey-7 outline")
                    if tok.field is None:  # $Skip
                        badge.props("color=grey-5").tooltip("Matches and discards a run")

            pattern_input = ui.input("Pattern", value=initial_pattern).props(
                "dense clearable"
            ).classes("w-full q-mt-sm")
            # Quick-pick recent patterns from a dropdown; managed (removed) in Settings.
            attach_history_menu(
                pattern_input, controller.ctx.config.recent_filename_templates,
                lambda p: p, lambda p: pattern_input.set_value(p),
                tooltip="Recent patterns",
            )

            fields_row = ui.row().classes("items-center w-full q-gutter-sm q-mt-sm")
            preview_box = ui.column().classes("w-full q-mt-sm")
            apply_btn = ui.button("Apply to selection", icon="done_all")

            def _render_fields(present: list[str]) -> None:
                fields_row.clear()
                with fields_row:
                    if not present:
                        return
                    ui.label("Write:").classes("text-caption colophon-muted self-center")
                    for name in present:
                        chosen.setdefault(name, True)
                        ui.checkbox(name, value=chosen[name]).props("dense").on_value_change(
                            lambda e, n=name: chosen.__setitem__(n, e.value)
                        )

            def _render_preview() -> None:
                preview_box.clear()
                pat = (pattern_input.value or "").strip()
                try:
                    compile_template(pat)
                except ValueError as e:
                    apply_btn.set_enabled(False)
                    with preview_box:
                        ui.label(f"Invalid pattern: {e}").classes("text-caption text-negative")
                    _render_fields([])
                    return
                # Fields the pattern can produce, in pattern order ($Skip excluded).
                present = _placeholder_fields(pat)
                _render_fields(present)
                apply_btn.set_enabled(bool(present))
                matched = 0
                with preview_box:
                    with ui.scroll_area().classes("w-full").style("max-height: 32vh"):
                        with ui.list().props("dense").classes("w-full"):
                            for b in books:
                                parsed = controller.preview_filename_parse(b, pat)
                                if parsed:
                                    matched += 1
                                # What apply would actually write (shares the
                                # controller's logic, so preview cannot mislead).
                                effective = controller.filename_parse_updates(b, pat, set(present))
                                with ui.item():
                                    with ui.item_section():
                                        ui.item_label(controller.book_filename(b)).classes("ellipsis")
                                        if not parsed:
                                            ui.item_label("no match").props("caption").classes(
                                                "colophon-muted"
                                            )
                                        elif effective:
                                            shown = ", ".join(f"{k}={v}" for k, v in effective.items())
                                            ui.item_label(shown).props("caption")
                                        else:
                                            ui.item_label("(no fields to write)").props(
                                                "caption"
                                            ).classes("colophon-muted")
                    ui.label(f"{matched} of {len(books)} filename(s) match").classes(
                        "text-caption colophon-muted"
                    )

            def _on_pattern_change() -> None:
                _render_preview()

            pattern_input.on_value_change(lambda _e: _on_pattern_change())

            def _apply() -> None:
                pat = (pattern_input.value or "").strip()
                fields = {n for n, on in chosen.items() if on}
                if not fields:
                    ui.notify("Select at least one field to write")
                    return
                try:
                    n = controller.apply_filename_parse(books, pat, fields)
                except ValueError as e:
                    ui.notify(f"Invalid pattern: {e}", type="negative")
                    return
                controller.record_filename_template(pat)
                ui.notify(f"Parsed and wrote fields to {n} of {len(books)} book(s)")
                _close()

            def _close() -> None:
                # Defer view refresh until close so rebuilding list_container can't
                # tear down this dialog while it is still open.
                dialog.close()
                refresh_nav()
                _render_middle()
                refresh_status()
                if selected_ids:
                    _after_select()

            with ui.row().classes("w-full justify-end q-gutter-sm q-mt-md"):
                ui.button("Cancel", on_click=dialog.close).props("flat")
                apply_btn.on_click(_apply)

            _render_preview()
        dialog.open()

    # --- navigator ---
    def _nav_item(
        label: str, icon: str, active: bool, on_click, color: str | None = None, *,
        checkbox=None, menu=None,
    ) -> None:
        with ui.item(on_click=on_click).props("clickable" + (" active" if active else "")):
            if checkbox is not None:
                checked, on_change = checkbox
                with ui.item_section().props("avatar"):
                    ui.checkbox(value=checked, on_change=on_change)
            with ui.item_section().props("avatar"):
                ui.icon(icon, color=color) if color else ui.icon(icon)
            with ui.item_section():
                ui.item_label(label)
            if menu is not None:
                with ui.item_section().props("side"):
                    # Kebab opens its own menu; click.stop keeps the row's
                    # on_click (scope navigation) from also firing.
                    with ui.button(icon="more_vert").props(
                        "flat dense round size=sm"
                    ).on("click.stop", lambda: None):
                        with ui.menu():
                            menu()

    def _entity_menu(kind: str, name: str, aliases: dict[tuple[str, str], str]) -> None:
        """Populate the kebab menu for an author/series/franchise nav node:
        Rename, Merge into, and (when this node is an alias target) Unmerge entries.
        `aliases` is the live alias map, fetched once per refresh by the caller."""

        def _after_alias(canonical: str | None) -> None:
            # An alias change can move books in/out of the scoped node. If the scoped
            # node was just aliased away (merge/rename), follow it to the canonical so
            # the main pane doesn't strand on a now-missing scope; otherwise just
            # re-render (and refresh the books pane when this kind is in scope).
            if canonical and scope["kind"] == kind and scope["key"] == name:
                _set_scope(kind, canonical)
            elif scope["kind"] == kind:
                refresh_nav()
                _render_middle()
            else:
                refresh_nav()

        def _apply_alias(value: str | None, dialog) -> None:
            canonical = (value or "").strip()
            if not canonical:  # never write an empty canonical name
                return
            controller.set_entity_alias(kind, name, canonical)
            dialog.close()
            _after_alias(canonical)

        def _clear_alias(source_key: str) -> None:
            controller.clear_entity_alias(kind, source_key)
            _after_alias(None)

        def _rename() -> None:
            with modal() as dlg, ui.card().classes("w-80"):
                ui.label("Rename entity").classes("text-subtitle1")
                name_in = ui.input("Name", value=name).props("dense").classes("w-full")
                with ui.row().classes("w-full justify-end q-gutter-sm q-mt-sm"):
                    ui.button("Cancel", on_click=dlg.close).props("flat")
                    ui.button(
                        "Rename", icon="edit",
                        on_click=lambda: _apply_alias(name_in.value, dlg),
                    )
            dlg.open()

        def _merge() -> None:
            with modal() as dlg, ui.card().classes("w-80"):
                ui.label("Merge into").classes("text-subtitle1")
                ui.label(f"Merge '{name}' into another entity.").classes(
                    "text-caption colophon-muted"
                )
                target_in = ui.input("Target name", placeholder="Target name").props(
                    "dense"
                ).classes("w-full")
                with ui.row().classes("w-full justify-end q-gutter-sm q-mt-sm"):
                    ui.button("Cancel", on_click=dlg.close).props("flat")
                    ui.button(
                        "Merge", icon="merge",
                        on_click=lambda: _apply_alias(target_in.value, dlg),
                    )
            dlg.open()

        ui.menu_item("Rename…", on_click=_rename)
        ui.menu_item("Merge into…", on_click=_merge)
        # Sources currently aliased to THIS node can be reset (unmerged).
        aliased = [
            src_key
            for (k, src_key), canonical in aliases.items()
            if k == kind and _name_key(canonical) == _name_key(name)
        ]
        if aliased:
            ui.separator()
            for src_key in sorted(aliased):
                ui.menu_item(
                    f"Unmerge {src_key}", on_click=lambda s=src_key: _clear_alias(s)
                )

    def refresh_nav() -> None:
        nav_container.clear()
        full = controller.library_tree()
        terms = book_filter["text"].lower().split()
        if terms or folder_filter["path"] is not None:
            # Narrow the navigator to the same books the list shows (folder ∧ text), so the two
            # panels never disagree. `visible` is the shared match set; None means no active filter.
            visible = {b.id for b in full.all_books if _in_folder(b) and _matches_filter(b, terms)}
            tree = controller.navigator_view(visible)
        else:
            tree = full
        nav_aliases = controller.ctx.aliases.all()  # fetched once for every node's kebab
        kind, key = scope["kind"], scope["key"]
        with nav_container:
            ui.switch(
                "Multiselect", value=view["multiselect"], on_change=lambda e: _set_multiselect(e.value)
            ).props("dense").classes("q-mb-sm")
            ui.toggle(
                {"author": "By author", "series": "By series", "franchise": "By franchise", "phase": "By phase"},
                value=view["group_by"],
                on_change=lambda e: _set_group_by(e.value),
            ).props("dense no-caps").classes("w-full q-mb-sm")
            multiselect = bool(view["multiselect"])

            def _node_checkbox(book_ids: list[str]):
                # (checked, on_change) for a navigator entry when multiselect is on;
                # None otherwise. Checked when all of the node's books are selected.
                if not multiselect:
                    return None
                checked = bool(book_ids) and all(i in selected_ids for i in book_ids)
                return (checked, lambda e, ids=book_ids: _toggle_node(ids, e.value))

            if multiselect:
                with ui.row().classes("items-center q-gutter-xs q-pb-xs"):
                    ui.button(
                        "Select all", icon="done_all",
                        on_click=lambda: _select_all([b.id for b in _books_for_scope()]),
                    ).props("flat dense no-caps")
                    ui.button("Deselect all", icon="remove_done", on_click=_deselect_all).props(
                        "flat dense no-caps"
                    ).set_enabled(bool(selected_ids))
            # Authors/series shown are limited to those with books in the active
            # folder filter (when one is set), mirroring the filtered Books list.
            needs_id = [b for b in tree.needs_id if _in_folder(b)]
            with ui.list().props("dense").classes("w-full"):
                all_label = "All books in folder" if folder_filter["path"] else "All books"
                _nav_item(all_label, "library_books", kind == "all", lambda: _set_scope("all", None))
                if needs_id:
                    _nav_item(
                        f"Needs identification ({len(needs_id)})",
                        "help_outline",
                        kind == "needs_id",
                        lambda: _set_scope("needs_id", None),
                        color="negative",
                        checkbox=_node_checkbox([b.id for b in needs_id]),
                    )
                attention = [
                    b for b in controller.books_needing_attention()
                    if _in_folder(b) and _matches_filter(b, terms)
                ]
                if attention:
                    counts = {"error": 0, "warn": 0, "info": 0}
                    for b in attention:
                        for f in controller._active_findings(b):
                            counts[f.severity.value] += 1
                    _nav_item(
                        f"Needs attention ({len(attention)})", "flag",
                        kind == "attention",
                        lambda: _set_scope("attention", None),
                        color="negative" if counts["error"] else "warning",
                        checkbox=_node_checkbox([b.id for b in attention]),
                    )
                if view["group_by"] == "phase":
                    universe = [b for b in tree.all_books if _in_folder(b)]
                    membership = controller.phase_membership(universe)
                    for phase in Phase:
                        group = membership[phase]
                        _nav_item(
                            f"{_PHASE_LABELS[phase]} ({len(group)})",
                            _PHASE_ICONS[phase],
                            kind == "phase" and key == phase.value,
                            lambda p=phase: _set_scope("phase", p.value),
                            checkbox=_node_checkbox([b.id for b in group]),
                        )
                elif view["group_by"] == "series":
                    for s in tree.series:
                        ids = [b.id for b in s.books if _in_folder(b)]
                        if not ids:
                            continue
                        _nav_item(
                            s.name,
                            "collections_bookmark",
                            kind == "series" and key == s.name,
                            lambda n=s.name: _set_scope("series", n),
                            checkbox=_node_checkbox(ids),
                            menu=lambda n=s.name: _entity_menu("series", n, nav_aliases),
                        )
                elif view["group_by"] == "franchise":
                    for f in tree.franchises:
                        ids = [b.id for b in f.books if _in_folder(b)]
                        if not ids:
                            continue
                        _nav_item(
                            f.name,
                            "hub",
                            kind == "franchise" and key == f.name,
                            lambda n=f.name: _set_scope("franchise", n),
                            checkbox=_node_checkbox(ids),
                            menu=lambda n=f.name: _entity_menu("franchise", n, nav_aliases),
                        )
                else:
                    for author in tree.authors:
                        aids = list(dict.fromkeys(  # dedup: a book may be in two of this author's series
                            [b.id for s in author.series for b in s.books if _in_folder(b)]
                            + [b.id for b in author.standalone if _in_folder(b)]
                        ))
                        if not aids:
                            continue  # no books from this author in the current folder
                        _nav_item(
                            author.name,
                            "person",
                            kind == "author" and key == author.name,
                            lambda name=author.name: _set_scope("author", name),
                            checkbox=_node_checkbox(aids),
                            menu=lambda n=author.name: _entity_menu("author", n, nav_aliases),
                        )
                view_entities = {"series": tree.series, "franchise": tree.franchises}.get(
                    str(view["group_by"]), tree.authors
                )
                if terms and view["group_by"] != "phase" and not view_entities:
                    ui.label("No matches").classes("colophon-muted text-caption q-pa-sm")

    def _update_count() -> None:
        n = len(selected_ids)
        middle_count.text = f"{n} selected" if n else ""

    def _set_filter(value: str | None) -> None:
        book_filter["text"] = value or ""
        refresh_list()
        refresh_nav()  # the filter is cross-panel: it narrows the navigator to the same books
        _persist_view()

    def _filter_to(label: str) -> None:
        """Filter the Books list to an exact genre/tag (clicked from a chip)."""
        book_filter["text"] = label
        search = refs.get("filter")
        if search is not None:
            search.set_value(label)
        refresh_list()
        refresh_nav()
        _persist_view()

    def _set_facet(name: str, value) -> None:
        view["facets"][name] = value
        _update_count()
        refresh_list()

    def _set_sort(value: str) -> None:
        view["sort"] = value or "none"
        _update_count()
        refresh_list()

    def _set_mode(value: str) -> None:
        view["mode"] = value
        view["sort"] = "conf_asc" if value == "triage" else "title"
        _render_middle()  # rebuild so the sort control reflects the new default, then refreshes

    def _render_middle() -> None:
        middle_title.text = "Books"
        # Show a folder-filter indicator in the Books header.
        middle_filter.clear()
        if folder_filter["path"]:
            folder = Path(str(folder_filter["path"]))
            with middle_filter:
                ui.icon("filter_alt", color="primary")
                ui.label(f"Filtered to {folder.name or folder}").classes(
                    "text-caption text-primary ellipsis"
                ).tooltip(str(folder))
                ui.button(icon="close", on_click=_clear_folder_filter).props(
                    'flat dense round size=sm color=primary aria-label="Clear folder filter"'
                ).tooltip("Clear folder filter")
        # Books toolbar: triage/browse mode + facet bar + free-text filter + selection controls.
        middle_toolbar.clear()
        with middle_toolbar:
            ui.toggle(
                {"triage": "Triage", "browse": "Browse"}, value=view["mode"],
                on_change=lambda e: _set_mode(e.value),
            ).props("dense no-caps").tooltip(
                "Triage: books needing attention, worst-confidence first. Browse: the whole scope."
            )
            with ui.row().classes("items-center w-full q-gutter-xs"):
                ui.select(
                    {"detected": "Detected", "identified": "Identified",
                     "needs_review": "Needs review", "ready": "Ready", "failed": "Failed"},
                    multiple=True, label="State", value=view["facets"]["state"],
                    on_change=lambda e: _set_facet("state", e.value),
                ).props("dense outlined options-dense").classes("col").style("min-width: 8.5rem")
                ui.select(
                    {"low": "<40", "mid": "40-74", "high": "≥75"},
                    multiple=True, label="Confidence", value=view["facets"]["confidence"],
                    on_change=lambda e: _set_facet("confidence", e.value),
                ).props("dense outlined options-dense").classes("col").style("min-width: 8.5rem")
                ui.select(
                    {"weak": "Weak", "trusted": "Trusted"}, label="Trust", clearable=True,
                    value=view["facets"]["trust"],
                    on_change=lambda e: _set_facet("trust", e.value),
                ).props("dense outlined options-dense").classes("col").style("min-width: 8.5rem")
                ui.select(
                    {"series": "No series", "cover": "No cover", "ident": "No ASIN/ISBN",
                     "narrator": "No narrator", "year": "No year"},
                    multiple=True, label="Missing", value=view["facets"]["missing"],
                    on_change=lambda e: _set_facet("missing", e.value),
                ).props("dense outlined options-dense").classes("col").style("min-width: 8.5rem")
                ui.select(
                    {"conf_asc": "Worst first", "conf_desc": "Best first", "title": "Title A-Z"},
                    label="Sort", value=view["sort"],
                    on_change=lambda e: _set_sort(e.value),
                ).props("dense outlined options-dense").style("min-width: 8.5rem; max-width: 11rem")
            ui.checkbox(
                "Open findings", value=view["facets"]["findings"],
                on_change=lambda e: _set_facet("findings", e.value),
            ).props("dense")
            search = filter_input(
                _set_filter,
                placeholder="Filter title, author, series, narrator, genre, tag, filename",
                value=book_filter["text"],
                aria_label="Filter the library",
            ).classes("w-full")
            refs["filter"] = search  # so the "/" shortcut can focus it
            with ui.row().classes("items-center w-full no-wrap q-gutter-xs"):
                ui.button("Select all", icon="done_all", on_click=_select_visible) \
                    .props("flat dense no-caps").tooltip("Select all books matching the filter")
                ui.button("Deselect visible", icon="remove_done", on_click=_deselect_visible) \
                    .props("flat dense no-caps").tooltip("Deselect only the books matching the current filter")
                ui.space()
                ui.button("Parse", icon="data_object", on_click=_parse_dialog) \
                    .props("flat dense no-caps").tooltip(
                        "Parse fields from the selected books' filenames"
                    )
        _update_count()
        refresh_list()

    def _set_group_by(value: str) -> None:
        view["group_by"] = value
        scope["kind"], scope["key"] = "all", None  # reset scope when switching grouping
        refresh_nav()
        _render_middle()
        _persist_view()

    def _set_multiselect(on: bool) -> None:
        # The navigator's Multiselect switch only toggles author/series-node
        # checkboxes; per-book selection in the Books pane is always available, so
        # toggling it must not disturb the current selection.
        view["multiselect"] = on
        refresh_nav()
        _persist_view()

    def _set_scope(kind: str, key) -> None:
        scope["kind"], scope["key"] = kind, key
        refresh_nav()
        _render_middle()
        _persist_view()

    def _clear_folder_filter() -> None:
        folder_filter["path"] = None
        refresh_nav()  # author/series list returns to the full library
        _render_middle()
        _persist_view()

    def _after_select() -> None:
        n = len(selected_ids)
        if n >= 2:
            show_bulk()
        elif n == 1:
            show_detail(next(iter(selected_ids)))
        else:
            show_detail("")

    def _toggle_node(book_ids: list[str], on: bool) -> None:
        # Multiselect operates on navigator entries (authors/series): checking a
        # node selects all of its books for bulk actions.
        if on:
            selected_ids.update(book_ids)
        else:
            selected_ids.difference_update(book_ids)
        refresh_list()  # reflect the change in the Books pane checkboxes
        refresh_status()
        _update_count()
        _after_select()
        _persist_view()

    def _undo() -> None:
        if controller.undo_last():
            ui.notify("Undid last change")
        else:
            ui.notify("Nothing to undo")
        _refresh_all()

    def refresh_status() -> None:
        status_container.clear()
        stats = controller.dashboard_stats()
        with status_container:
            ui.icon("library_books").classes("colophon-muted")
            ui.label(f"{stats.get('total', 0)} books").classes("text-caption")
            for state, label, color in _STATUS_BADGES:
                count = stats.get(state, 0)
                if count:
                    ui.badge(f"{label} {count}").props(f"color={color}")
            ui.space()
            if selected_ids:
                ui.label(f"{len(selected_ids)} selected").classes("text-caption colophon-muted")
                ui.button(
                    "Clear all selected", icon="clear_all", on_click=_clear_selection,
                ).props("flat dense").tooltip("Deselect every book, including any outside the current view")
            ui.button("Undo", icon="undo", on_click=_undo).props("flat dense")

    # Set when the header pipeline stepper is built (below); called on every global refresh so the
    # per-stage counts stay current after a scan / match / persist.
    _stepper_refresh: dict[str, object] = {"fn": lambda: None}

    def _refresh_all() -> None:
        with span("refresh_all"):
            with span("nav render"):
                refresh_nav()
            with span("middle render"):
                _render_middle()
            with span("status render"):
                refresh_status()
            _stepper_refresh["fn"]()
            # Keep an open bulk editor truthful after a global refresh (undo, scan, etc.).
            if len(selected_ids) >= 2:
                show_bulk()

    # --- async actions ---
    def _ui_safe(fn) -> None:
        """Run a UI update, swallowing the RuntimeError NiceGUI raises when the
        browser client has disconnected (its elements are gone). Without this, a
        long action whose client drops mid-run would dump a traceback from the
        post-action notify/refresh."""
        try:
            fn()
        except RuntimeError:
            logger.info("skipped a UI update; the client appears to have disconnected")

    async def _run(button, action, done_msg: str) -> None:
        _ui_safe(lambda: button.props("loading=true"))
        failed = False
        try:
            await action()
        except RuntimeError:
            # The action's own UI calls raised because the client disconnected; the
            # work itself ran. Nothing left to update.
            logger.exception("workspace action ended with RuntimeError (client gone?)")
            return
        except Exception:
            logger.exception("workspace action failed")
            failed = True
        _ui_safe(lambda: button.props(remove="loading"))
        _ui_safe(
            (lambda: ui.notify("Action failed (see logs)", type="negative"))
            if failed
            else (lambda: ui.notify(done_msg))
        )
        _ui_safe(_refresh_all)

    async def _maybe_auto_scan() -> None:
        """First-open bootstrap: if a configured scan path has no graph yet, scan it in the
        background — progress shows in a slim footer chip (the nav/list/detail stay usable).
        A result with no new books is applied silently; a result that discovers new books is
        held for a one-click confirm so a library-size change never lands silently. Guarded
        once per process so reconnects / an unscannable path can't loop."""
        global _auto_scan_attempted
        if _auto_scan_attempted:
            return
        missing = controller.scan_paths_missing_graph()
        if not missing:
            return
        _auto_scan_attempted = True

        prog: dict = {}

        def _show_scanning() -> None:
            scan_status.clear()
            with scan_status:
                ui.spinner(size="sm")
                prog["el"] = ui.label("Scanning library…").props(
                    "role=status aria-live=polite"
                ).classes("text-caption colophon-muted")

        _ui_safe(_show_scanning)

        def _progress(done: int, total: int, label: str) -> None:
            el = prog.get("el")
            if el is not None:
                _ui_safe(lambda: el.set_text(f"Scanning library… {done} / {total}"))

        try:
            plan = await controller.scan_preview_streamed(missing, progress=_progress)
        except Exception:  # log + clear; never block (BLE001 intentional)
            logger.exception("auto-scan on empty graph failed")
            _ui_safe(scan_status.clear)
            return

        if not auto_scan_needs_confirmation(plan):
            await asyncio.to_thread(controller.apply_scan, plan)  # off-thread: DB write + sweep
            _ui_safe(scan_status.clear)
            _ui_safe(_refresh_all)
            return

        async def _do_import() -> None:
            _ui_safe(scan_status.clear)
            await asyncio.to_thread(controller.apply_scan, plan)
            _ui_safe(_refresh_all)

        def _show_notice() -> None:
            scan_status.clear()
            with scan_status:
                ui.icon("library_add", color="primary")
                ui.label(f"Found {plan.new_books} new audiobooks").classes("text-caption")
                ui.button("Import", on_click=_do_import).props("flat dense no-caps color=primary")
                ui.button("Dismiss", on_click=lambda: _ui_safe(scan_status.clear)).props(
                    "flat dense no-caps"
                )

        _ui_safe(_show_notice)

    # --- application shell ---
    # Keyboard navigation for the Books list (ignored while typing in a field).
    ui.keyboard(on_key=_on_key)

    async def _on_save_key(e) -> None:
        if not e.action.keydown or not (e.modifiers.ctrl or e.modifiers.meta):
            return
        if (e.key.name or "").lower() != "s":
            return
        if editor_state["save"] is None:
            return
        if e.modifiers.shift:
            await editor_state["write"]()
        else:
            editor_state["save"]()

    ui.keyboard(on_key=_on_save_key, ignore=[])
    # Run via run_javascript (not a <script> in add_head_html): on the async index
    # page the head HTML is injected after load, and browsers do not execute script
    # tags inserted that way. The guard keeps it idempotent across reconnects.
    ui.run_javascript(
        "(function(){"
        "if(window.__colophonKeyguardReady)return;window.__colophonKeyguardReady=true;"
        "document.addEventListener('keydown', function(e){"
        " if ((e.ctrlKey || e.metaKey) && (e.key === 's' || e.key === 'S')) e.preventDefault();"
        "}, true);"
        "window.__colophon_dirty = false;"
        "window.addEventListener('beforeunload', function(e){"
        " if (window.__colophon_dirty) { e.preventDefault(); e.returnValue = ''; }"
        "});"
        "})();"
    )

    async def _do_scan() -> None:
        await scan_dialog(
            controller, refresh_all=_refresh_all,
            folder=Path(folder_filter["path"]) if folder_filter["path"] else None,
            selected_ids=set(selected_ids),
        )

    async def _do_match() -> None:
        await match_dialog(controller, refresh_all=_refresh_all, selected_ids=set(selected_ids))

    async def _do_persist() -> None:
        await persist_dialog(controller, refresh_all=_refresh_all, selected_ids=set(selected_ids),
                             clear_selection=selected_ids.clear)

    with ui.header(elevated=True).classes("items-center q-px-md"):
        ui.icon("auto_stories", color="primary").classes("text-h5")
        ui.label("Colophon").classes("text-h6 q-ml-sm text-weight-medium")
        app_tabs(controller, "library")
        ui.space()

        # The pipeline stepper: Scan -> Match -> Persist. Each stage is an action; the connectors
        # read as the guided path. Match/Persist show a live readiness count.
        _stage_badges: dict[str, object] = {}

        def _stage(label, icon, key, on_click, *, primary=False) -> None:
            btn = ui.button(on_click=on_click).props(
                "no-caps " + ("unelevated" if primary else "flat")
            ).classes("colophon-stage")
            with btn, ui.row().classes("items-center no-wrap q-gutter-xs"):
                ui.icon(icon)
                ui.label(label)
                if key:
                    _stage_badges[key] = ui.badge("").props("color=grey-7 rounded")

        _stage("Scan", "radar", None, _do_scan)
        ui.icon("chevron_right").classes("colophon-muted")
        _stage("Match", "join_inner", "identified", _do_match)
        ui.icon("chevron_right").classes("colophon-muted")
        _stage("Persist", "save", "ready", _do_persist, primary=True)

        def _refresh_stepper() -> None:
            counts = controller.pipeline_counts()
            for key, badge in _stage_badges.items():
                badge.set_text(str(counts.get(key, 0)))

        _refresh_stepper()
        _stepper_refresh["fn"] = _refresh_stepper

        jobs_indicator(controller)
        dark_mode_button(dark)
        ui.button(
            icon="view_column",
            on_click=lambda: ui.run_javascript("window.colophonResetColumns && colophonResetColumns()"),
        ).props("flat round").tooltip("Reset column widths")

    # The navigator is an in-content card rather than ui.left_drawer: the drawer
    # syncs its open state with a JavaScript round-trip on connect (1.0s timeout)
    # which fails over remote/high-latency connections. A card avoids that.
    # The cards fill the row's height via items-stretch, NOT height:100%. The row's
    # height comes from flex:1, which is not a definite reference for a percentage
    # height, so height:100% would collapse each card to its content and flatten the
    # internal scroll-areas to 0. Stretch sizes them correctly and the scroll-areas
    # absorb any overflow.
    with ui.row().classes("w-full no-wrap q-pa-md items-stretch").style(
        "flex: 1; min-height: 0; gap: 0"
    ):
        with ui.card().classes("column colophon-pane-nav"):
            ui.label("Library").classes("text-subtitle1")
            ui.separator()
            with ui.scroll_area().classes("col"):
                nav_container = ui.column().classes("w-full gap-0")
        ui.element("div").classes("colophon-resizer").tooltip("Drag to resize")
        with ui.card().classes("column colophon-pane-mid"):
            with ui.row().classes("items-center w-full no-wrap"):
                middle_title = ui.label("Books").classes("text-subtitle1")
                middle_filter = ui.row().classes("items-center q-gutter-xs q-ml-sm no-wrap")
                ui.space()
                middle_count = ui.label("").classes("text-caption colophon-muted")
            middle_toolbar = ui.column().classes("w-full gap-1 q-mt-xs")
            ui.separator().classes("q-mt-xs")
            list_scroll = ui.scroll_area(on_scroll=_on_list_scroll).classes("col colophon-book-scroll")
            with list_scroll:
                list_container = ui.column().classes("w-full gap-0")
        ui.element("div").classes("colophon-resizer").tooltip("Drag to resize")
        with ui.card().classes("col column"):
            ui.label("Details").classes("text-subtitle1")
            ui.separator()
            with ui.scroll_area().classes("col"):
                detail_container = ui.column().classes("w-full gap-1")

    with ui.footer().classes("q-px-md q-py-xs"):
        with ui.column().classes("w-full gap-0"):
            scan_status = ui.row().classes("items-center no-wrap q-gutter-xs")  # empty unless a scan-on-open is active
            status_container = ui.row().classes("items-center w-full no-wrap q-gutter-sm")

    _refresh_all()
    if _restored.open_book_id is not None:
        _ensure_rendered(_restored.open_book_id)
        show_detail(_restored.open_book_id)  # reopen the book remembered for this tab
    else:
        show_detail("")  # initial empty-state in the detail pane

    # Lazily bootstrap an unscanned library on first open (no-op if already scanned).
    ui.timer(0.1, _maybe_auto_scan, once=True)
