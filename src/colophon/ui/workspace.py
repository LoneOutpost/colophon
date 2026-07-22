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
from urllib.parse import quote

from nicegui import app, background_tasks, ui

from colophon.controller import AppController, RerunResult
from colophon.core.attention import attention_items
from colophon.core.audio_quality import book_quality_summary, format_file_quality
from colophon.core.book_search import (
    FIELDS,
    Condition,
    book_matches,
    build_token,
    field_label,
    format_query,
    parse_query,
)
from colophon.core.chapters import file_boundary_chapters
from colophon.core.fields import EDITABLE_FIELDS, field_provenance, get_field
from colophon.core.filename_parser import compile_template
from colophon.core.graph_resolve import _name_key
from colophon.core.guidance import FixAction
from colophon.core.models import BookState, BookUnit, FindingSeverity, Phase, PhaseState
from colophon.core.normalize import FIELD_NORMALIZERS, NORMALIZABLE_FIELDS
from colophon.core.perf import span
from colophon.core.state_labels import state_badge_tooltip, state_description
from colophon.core.tokens import PARSE_TOKENS, parse_field_for
from colophon.core.triage import (
    FACET_DEFAULTS,
    WEAK_ID_TRUST_TIERS,
    apply_facets,
    blocking_reason,
    has_blocking_error,
    has_open_findings,
    needs_human,
    sort_books,
    weak_identity_reason,
)
from colophon.core.view_state import snapshot_to_view, view_to_snapshot
from colophon.services.ingest import auto_scan_needs_confirmation
from colophon.ui import state_panel
from colophon.ui.chrome import brand_mark, empty_state, jobs_indicator
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
    remove_from_library_dialog,
    rename_dialog,
    scan_dialog,
    tag_dialog,
)
from colophon.ui.filter_input import filter_input
from colophon.ui.graph_view import (
    combine_folder_dialog,
    nodes_url_for_book,
    reclassify_folder_dialog,
)
from colophon.ui.skeleton import skeleton_rows
from colophon.ui.state_panel import _PHASE_ICONS, _PHASE_LABELS
from colophon.ui.tabs import app_tabs
from colophon.ui.theme import dark_mode_button

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
_PAGE = 50  # book rows rendered per chunk; ~a viewport, the rest fill in on scroll
_NAV_PAGE = 80  # navigator entity rows (author/series/franchise) rendered per chunk

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

# The State facet filter's options — one per BookState, in lifecycle order. Kept exhaustive: a
# missing state silently hides those books from the filter (Encoded/Organized/Skipped were absent).
# A test pins this to the full BookState enum so it can't drift again.
_STATE_FILTER_OPTIONS: dict[str, str] = {
    BookState.DETECTED.value: "Detected",
    BookState.IDENTIFIED.value: "Identified",
    BookState.NEEDS_REVIEW.value: "Needs review",
    BookState.READY.value: "Ready",
    BookState.ENCODING.value: "Encoding",
    BookState.ENCODED.value: "Encoded",
    BookState.ORGANIZED.value: "Organized",
    BookState.FAILED.value: "Failed",
    BookState.SKIPPED.value: "Skipped",
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


def _state_badge(book: BookUnit) -> None:
    """Render the per-book state badge. The tooltip always explains the state, and appends
    the specific review reasons when the book is uncertain."""
    label, color = _STATE_BADGE.get(book.state, (book.state.value, "grey-6"))
    ui.badge(label).props(f"color={color} outline").tooltip(state_badge_tooltip(book))


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


def render_workspace(controller: AppController, dark: ui.dark_mode, initial_filter: str = "") -> None:
    # Theme + dark-mode are applied by the page handler *before* it awaits the client
    # (so they ship in the initial HTML and the page doesn't flash the light theme);
    # `dark` is passed in for the header toggle rather than created here.
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
    # Navigator entity rows are windowed the same way the book list is: a slice is
    # rendered up front and the rest fill in on scroll. `pending` holds one zero-arg
    # render closure per entity; `el` is the ui.list() they render into.
    _nav_view: dict[str, object] = {"pending": [], "rendered": 0, "el": None}
    _list_footer: dict[str, object] = {"el": None}  # the "Showing X of Y" caption
    list_scroll = None                              # assigned at the layout site
    detail_scroll = None                            # the detail pane's scroll area (layout site)
    # The detail pane's tab and scroll offset persist across book navigation so cycling
    # books (arrow keys or clicking) stays on the same tab and, on At a Glance, holds the
    # scroll position instead of snapping back to Details at the top.
    _detail_view = {"tab": "details", "scroll": 0.0}

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

    view["facets"] = dict(FACET_DEFAULTS)
    view["sort"] = "title"

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

    def _matches_filter(book, conditions: list[Condition]) -> bool:
        if not conditions:
            return True
        filename = controller.book_filename(book)
        any_haystack = f"{book_haystack(book)} {filename.lower()}"
        return book_matches(book, conditions, filename=filename, any_haystack=any_haystack)

    def _scoped_books() -> list:
        """Scope ∧ folder ∧ text — the candidate set before facets/sort."""
        books = _books_for_scope()
        conditions = parse_query(book_filter["text"])
        if conditions:
            books = [b for b in books if _matches_filter(b, conditions)]
        return books

    def _visible_books() -> list:
        """Scope ∧ folder ∧ text, then the active facets and the chosen sort."""
        return sort_books(apply_facets(_scoped_books(), view["facets"]), view["sort"])

    # --- attention pane (findings + guided actions) ---
    def render_attention_pane(book) -> None:
        items = attention_items(book, controller._active_findings(book))
        with ui.column().classes("w-full gap-2"):
            for item in items:
                icon, color = _SEVERITY_BADGE[item.severity]
                with ui.row().classes("items-center gap-2"):
                    ui.icon(icon).props(f"color={color}")
                    ui.label(item.detail)
                ui.label(item.suggestion).classes("colophon-muted text-caption")
                with ui.row().classes("gap-2"):
                    if FixAction.ACKNOWLEDGE in item.actions and item.code is not None:
                        def _ack(c=item.code, b=book) -> None:
                            controller.acknowledge_finding(b, c)
                            ui.notify("Acknowledged", type="info")
                            repaint(nav=True, middle=True)
                        ui.button("Acknowledge", on_click=_ack).props("flat color=primary")
                    if FixAction.DELETE in item.actions:
                        def _del(b=book) -> None:
                            _delete_book_items(b)
                        ui.button("Delete", icon="delete", on_click=_del).props("flat color=negative")

    def _delete_book_items(book) -> None:
        from colophon.core.classify import corrupt_source_files
        from colophon.ui.dialogs import confirm_delete_dialog

        if book.missing:
            paths, book_removed = [], True
        else:
            paths = corrupt_source_files(book.source_files)
            book_removed = bool(paths) and len(paths) == len(book.source_files)

        def _run() -> None:
            if book.missing:
                controller.remove_missing(book)
                removed = True
            else:
                result = controller.delete_corrupt_files(book)
                removed = result.book_removed
                if result.errors:
                    ui.notify("; ".join(result.errors), type="warning")
            ui.notify("Deleted", type="info")
            refresh_list()
            if removed:
                _clear_selection()
            else:
                show_detail(book.id)

        confirm_delete_dialog(paths, book_removed=book_removed, on_confirm=_run)

    # --- detail pane ---
    def show_detail(book_id: str) -> None:
        if _guard_nav(book_id, lambda: show_detail(book_id)):
            return
        detail_container.clear()
        detail_actions.clear()
        book = controller.get_book(book_id)
        with detail_container:
            if book is None:
                _clear_editor_state()
                with empty_state(
                    "menu_book", "Select a book",
                    "Pick a title from the list to view its metadata, match it against "
                    "sources, edit fields, and set the cover.",
                ):
                    pass
                return

            def _details_body() -> None:
                # editable fields, each prefilled with its value + provenance badge
                inputs: dict[str, ui.input | ui.textarea] = {}
                originals: dict[str, str] = {}
                autocomplete = {
                    "author": controller.known_authors(),
                    "series": controller.known_series(),
                    "franchise": controller.known_franchises(),
                }

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
                    repaint(list=True, nav=True, detail_book_id=b.id)

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

                blocked = has_blocking_error(book)
                block_tip = blocking_reason(book) if blocked else None
                # Render the primary actions into the fixed slot ABOVE the scroll area
                # (detail_actions), not inline, so Save / Write tags / Mark ready stay
                # visible from anywhere in a long editor. These closures keep access to
                # _save / _save_pending even though they mount in an outer container.
                with detail_actions:
                    ui.button("Save", icon="save", on_click=_save).props("unelevated")
                    write_btn = ui.button("Write tags", icon="sell", on_click=lambda b=book: tag_dialog(controller, b, refresh_list=refresh_list, refresh_status=refresh_status, save_pending=lambda: _save_pending(b))).props("outline")
                    write_btn.set_enabled(not blocked)
                    if block_tip:
                        write_btn.tooltip(f"Can't persist — {block_tip}")
                    ui.space()
                    ready_btn = ui.button(
                        "Mark ready", icon="check",
                        # Rebuild the detail panel too (not just the list) so the state badge flips to
                        # Ready right away — otherwise the panel looks unchanged and the action seems
                        # to do nothing. Matches how applying a match refreshes.
                        on_click=lambda b=book: (
                            controller.mark_ready(b), ui.notify("Marked ready"),
                            refresh_list(), show_detail(b.id),
                        ),
                    ).props("flat")
                    ready_btn.set_enabled(not blocked)
                    if block_tip:
                        ready_btn.tooltip(f"Can't mark ready — {block_tip}")

                with ui.row().classes("w-full no-wrap items-start q-gutter-md"):
                    # Left aside: cover, status, location.
                    with ui.column().classes("items-center q-gutter-xs").style("width: 120px; flex: 0 0 120px"):
                        _render_cover(book, width=112, height=168, icon="text-h2")
                        _cval, _ccolor, _ctip = _primary_confidence(book, controller.review_threshold())
                        ui.badge(f"{_cval:.0f}").props(f"color={_ccolor}").tooltip(_ctip)
                        _state_badge(book)
                    # Main column: title, source path, tools, grouped fields.
                    with ui.column().classes("col q-gutter-none").style("min-width: 0"):
                        with ui.row().classes("items-center no-wrap w-full q-gutter-xs"):
                            ui.label(book.title or "(untitled)").classes(
                                "colophon-book-title text-h6 col ellipsis"
                            )
                            ui.button(
                                icon="hub",
                                on_click=lambda b=book: ui.navigate.to(nodes_url_for_book(b.id)),
                            ).props('flat dense round aria-label="Show in the graph"').tooltip(
                                "Show this book in the graph"
                            )
                        if book.source_folder is not None:
                            with ui.row().classes("items-center no-wrap w-full q-gutter-xs q-mb-xs"):
                                ui.icon("folder", size="0.875rem").classes("colophon-muted")
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
                                    ).classes("colophon-signal").tooltip(sig.detail)

                        # Attention sits up top, right under the confidence read-out, so the score,
                        # the signals behind it, and the problems that need a human read as one story.
                        if controller._active_findings(book) or book.missing:
                            with ui.element("div").classes("colophon-attention w-full q-mb-sm"):
                                with ui.row().classes("items-center q-gutter-xs q-mb-xs"):
                                    ui.icon("warning_amber", size="1.125rem").classes("text-warning")
                                    ui.label("Attention").classes("text-subtitle2")
                                render_attention_pane(book)
                                ui.button(
                                    "Full details in At a Glance", icon="visibility",
                                    on_click=lambda: _tabs.set_value("state"),
                                ).props("flat dense no-caps").classes("q-mt-xs")

                        # --- metadata tool groups ---
                        with ui.row().classes("w-full q-mb-sm colophon-toolgroups"):
                            with ui.element("div").classes("colophon-toolgroup"):
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
                                            repaint(list=True, status=True, detail_book_id=b.id)
                                        ui.button("Recheck Confidence", icon="refresh", on_click=_recheck).props(
                                            "flat dense no-caps"
                                        ).tooltip("Re-query sources and revert to the computed confidence")
                                    else:
                                        def _confirm(b=book) -> None:
                                            controller.confirm_confidence(b)
                                            repaint(list=True, status=True, detail_book_id=b.id)
                                        ui.button("Manual Confirmation", icon="verified", on_click=_confirm).props(
                                            "flat dense no-caps"
                                        ).tooltip("Confirm this book and set its confidence to 100%")
                            with ui.element("div").classes("colophon-toolgroup"):
                                ui.label("Clean up").classes("colophon-seccap")
                                with ui.row().classes("q-gutter-xs"):
                                    ui.button("Normalize", icon="text_format", on_click=_normalize_all).props("flat dense no-caps").tooltip("Normalize all text fields")
                                    ui.button("Remap", icon="swap_horiz", on_click=lambda b=book: remap_dialog(controller, b, refresh_list=refresh_list, show_detail=show_detail)).props("flat dense no-caps").tooltip("Move one field's value to another")
                                    _folder_kind = controller.folder_classification(book.source_folder)
                                    ui.button(
                                        "Reclassify", icon="sell",
                                        on_click=lambda b=book, k=_folder_kind: reclassify_folder_dialog(
                                            controller, b.source_folder, k,
                                            on_done=lambda i=b.id: (refresh_list(), show_detail(i))),
                                    ).props("flat dense no-caps").tooltip(
                                        f"Reclassify this book's folder (now: {_folder_kind or 'unclassified'}). "
                                        "Use when a book was mistaken for an author.")
                                    if len(controller.folder_books(book.source_folder)) > 1:
                                        ui.button(
                                            "Combine", icon="merge",
                                            on_click=lambda b=book: combine_folder_dialog(
                                                controller, b.source_folder,
                                                on_done=lambda i=b.id: (refresh_list(), show_detail(i))),
                                        ).props("flat dense no-caps").tooltip(
                                            "Combine this folder's files into one book (chapters). "
                                            "Use when one book was split into many.")
                                    elif controller.folder_is_combined(book.source_folder):
                                        def _uncombine(b=book) -> None:
                                            controller.uncombine_folder(b.source_folder)
                                            ui.notify("Split back into separate books")
                                            refresh_list()
                                        ui.button("Uncombine", icon="call_split", on_click=_uncombine) \
                                            .props("flat dense no-caps").tooltip(
                                                "Split this combined book back into separate books")
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
                                            repaint(list=True, status=True, detail_book_id=b.id)
                                        ui.button("Remove missing", icon="delete_outline", on_click=_remove_missing).props(
                                            "flat dense no-caps color=negative"
                                        ).tooltip("Delete this orphaned record (the folder is gone)")
                            else:
                                with ui.element("div").classes("colophon-toolgroup"):
                                    ui.label("Library").classes("colophon-seccap")
                                    with ui.row().classes("q-gutter-xs"):
                                        def _remove_book(b=book) -> None:
                                            remove_from_library_dialog(
                                                controller,
                                                [b.id],
                                                label=f'"{b.title or Path(b.source_folder).name}"',
                                                on_done=lambda bid=b.id: repaint(
                                                    nav=True, list=True, status=True,
                                                    detail_book_id=bid,
                                                ),
                                            )
                                        ui.button(
                                            "Remove from library", icon="delete_outline",
                                            on_click=_remove_book,
                                        ).props("flat dense no-caps color=negative").tooltip(
                                            "Forget this book (files stay on disk)"
                                        )

                        # --- grouped fields ---
                        ui.label("Identity").classes("colophon-seccap")
                        with ui.grid(columns=2).classes("w-full"):
                            for f in ("title", "subtitle", "author", "narrator", "series",
                                      "sequence", "franchise"):
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

                editor_state.update(
                    book_id=book_id, is_dirty=_is_dirty,
                    save_pending=_save_pending, save=_save,
                    write=lambda b=book: tag_dialog(controller, b, refresh_list=refresh_list, refresh_status=refresh_status, save_pending=lambda: _save_pending(b)),
                )
                _set_dirty(False)
                _persist_view()

                if book.source_files:
                    ui.separator().classes("q-my-sm")
                    ui.label(f"Files ({len(book.source_files)})").classes("text-subtitle2")

                    with ui.list().props("dense bordered").classes("w-full"):
                        for idx, sf in enumerate(book.source_files):
                            with ui.item():
                                with ui.item_section():
                                    ui.item_label(sf.path.name)
                                    quality = format_file_quality(sf)
                                    dur = _fmt_duration(sf.duration_seconds)
                                    caption = f"{dur} · {quality}" if quality else dur
                                    ui.item_label(caption).props("caption")
                                with ui.item_section().props("side"):
                                    with ui.row().classes("q-gutter-xs no-wrap"):
                                        ui.button(icon="arrow_upward", on_click=lambda p=sf.path: (controller.move_file(book, p, -1), show_detail(book.id))).props('flat dense round aria-label="Move file up"').tooltip("Move file up").set_enabled(idx > 0)
                                        ui.button(icon="arrow_downward", on_click=lambda p=sf.path: (controller.move_file(book, p, 1), show_detail(book.id))).props('flat dense round aria-label="Move file down"').tooltip("Move file down").set_enabled(idx < len(book.source_files) - 1)
                                        ui.button(icon="edit", on_click=lambda p=sf.path: rename_dialog(controller, book, p, show_detail=show_detail)).props('flat dense round aria-label="Rename file"').tooltip("Rename file")
                                        ui.button(icon="remove_circle_outline", on_click=lambda p=sf.path: (controller.exclude_file(book, p), ui.notify("Excluded"), show_detail(book.id))).props('flat dense round color=negative aria-label="Exclude file"').tooltip("Exclude this file from the book")

                    siblings = controller.folder_sibling_files(book)
                    if siblings:
                        ui.label("Other files in this folder").classes("text-subtitle2 q-mt-sm")
                        with ui.list().props("dense bordered").classes("w-full"):
                            for sib_path, owner in siblings:
                                with ui.item():
                                    with ui.item_section():
                                        ui.item_label(sib_path.name)
                                        ui.item_label(f"in {owner.title or owner.source_folder.name}").props("caption")
                                    with ui.item_section().props("side"):
                                        ui.button(
                                            icon="playlist_add",
                                            on_click=lambda p=sib_path: (
                                                controller.reassign_file(book, p),
                                                ui.notify("Added to this book"),
                                                show_detail(book.id),
                                            ),
                                        ).props('flat dense round color=primary aria-label="Add to this book"').tooltip("Add this file to this book")

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
                ui.tab("state", label="At a Glance", icon="visibility")
            # Reopen on the tab the user last had, so navigating books doesn't yank them
            # off At a Glance back to Details.
            _tabs.on_value_change(lambda e: _detail_view.update(tab=e.value))
            with ui.tab_panels(_tabs, value=_detail_view["tab"]).classes("w-full"):
                with ui.tab_panel("details").classes("q-pa-none"):
                    _details_body()
                with ui.tab_panel("state").classes("q-pa-none"):
                    async def _organize(b=book) -> None:
                        await persist_dialog(
                            controller, refresh_all=_refresh_all,
                            selected_ids={b.id}, clear_selection=_clear_selection,
                        )

                    def _reprobe(b=book) -> None:
                        if controller.reprobe_book(b):
                            ui.notify("Re-probed — the file now reads")
                        else:
                            ui.notify(
                                "Still no readable audio — replace the file first", type="warning"
                            )
                        refresh_list()
                        show_detail(b.id)

                    async def _rerun_one(b: BookUnit, phase: Phase) -> None:
                        result = await asyncio.to_thread(controller.rerun_phase, [b], phase)
                        _rerun_notify(result)
                        repaint(list=True, status=True, detail_book_id=b.id)

                    _attn = state_panel.AttentionActions(
                        acquire=lambda b=book: ui.navigate.to(f"/acquire?book={quote(b.id)}"),
                        reprobe=_reprobe,
                        organize=_organize,
                        files=lambda: _tabs.set_value("details"),
                        matches=lambda b=book: compare_dialog(
                            controller, b, show_detail=show_detail, refresh_list=refresh_list),
                        acknowledge=lambda code, b=book: (
                            controller.acknowledge_finding(b, code), refresh_list(),
                            show_detail(b.id)),
                        delete=lambda b=book: _delete_book_items(b),
                        rerun_phase=_rerun_one,
                    )
                    state_panel.render(controller, book, actions=_attn)

        # Cycling books on At a Glance holds the scroll offset; Details opens at the top.
        if _detail_view["tab"] == "state" and _detail_view["scroll"] and detail_scroll is not None:
            detail_scroll.scroll_to(pixels=_detail_view["scroll"])

    def _clear_selection() -> None:
        """Clear the ENTIRE selection across every view and collapse the bulk
        panel. The single canonical 'clear everything' used by the footer button,
        the bulk Clear selection button, the navigator Deselect all, and after a
        bulk operation runs on the selection."""
        selected_ids.clear()
        repaint(nav=True, list=True, status=True)
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
            autocomplete = {
                "author": controller.known_authors(),
                "series": controller.known_series(),
                "franchise": controller.known_franchises(),
            }
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
                rerun_btn = ui.button("Re-run phase", icon="refresh").props("outline")

                async def _rerun_selection(phase: Phase) -> None:
                    _ui_safe(lambda: rerun_btn.props("loading=true"))
                    try:
                        result = await asyncio.to_thread(controller.rerun_phase, books, phase)
                    finally:
                        _ui_safe(lambda: rerun_btn.props(remove="loading"))
                    _rerun_notify(result)
                    repaint(nav=True, list=True, status=True)

                with rerun_btn, ui.menu():
                    for _p in (Phase.SEARCH, Phase.CATEGORIZE, Phase.IDENTIFY):
                        ui.menu_item(
                            f"Re-run {state_panel.phase_label(_p)}",
                            lambda p=_p: _rerun_selection(p),
                        )
                ui.button(
                    "Re-identify…", icon="badge", on_click=lambda: _reidentify_dialog(),
                ).props("outline").tooltip(
                    "Re-read the selection's filenames with a chosen pattern (e.g. $PubYear - $Title)")

                def _remove_selection() -> None:
                    ids = [b.id for b in books]
                    remove_from_library_dialog(
                        controller,
                        ids,
                        label=f"{len(ids)} books",
                        on_done=_clear_selection,  # already repaints nav/list/status
                    )

                ui.button(
                    "Remove from library", icon="delete_outline",
                    on_click=_remove_selection,
                ).props("outline color=negative").tooltip(
                    "Forget the selected books (files stay on disk)"
                )
                ui.button(
                    "Clear selection", icon="clear", on_click=_clear_selection,
                ).props("flat")

    # --- book list ---
    def _select_all(book_ids: list[str]) -> None:
        # Navigator "Select all": all books in the current scope (ignores filter).
        selected_ids.update(book_ids)
        repaint(nav=True, list=True, status=True)
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
        repaint(nav=True, list=True, status=True)
        _update_count()
        _after_select()
        _persist_view()

    def _deselect_visible() -> None:
        # Books-header "Deselect all": subtractive over the filtered, visible books;
        # selections outside the current filter are left untouched.
        selected_ids.difference_update(b.id for b in _visible_books())
        repaint(nav=True, list=True, status=True)
        _update_count()
        _after_select()
        _persist_view()

    def _any_visible_selected() -> bool:
        """Whether any currently-visible (filtered) book is selected. Uses the cached visible list
        so it stays cheap on every selection toggle."""
        return any(b.id in selected_ids for b in _list_view["books"])

    def _deselect_smart() -> None:
        # Cycle: clear what's visible first; once nothing visible is selected, clear the rest
        # (books hidden by the current filter). So a user never has to leave the filter, go to All
        # books, and Deselect all just to drop a stray hidden selection.
        if _any_visible_selected():
            _deselect_visible()
        else:
            _deselect_all()

    def _sync_deselect_btn() -> None:
        """Label the Books-header deselect button for what it will do next: 'Deselect visible' while
        visible books are selected, else 'Deselect all' (clears the filtered-out remainder)."""
        btn = refs.get("deselect_btn")
        if btn is not None:
            btn.set_text("Deselect visible" if _any_visible_selected() else "Deselect all")

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
                    quality = book_quality_summary(book.source_files)
                    if quality:
                        _qcls = "text-warning" if quality == "Mixed quality" else "colophon-muted"
                        ui.label(quality).classes(f"text-caption {_qcls} colophon-mono").tooltip(
                            "Audio quality across this book's files"
                        )
                    _cval, _ccolor, _ctip = _primary_confidence(book, controller.review_threshold())
                    ui.badge(f"{_cval:.0f}").props(f"color={_ccolor}").tooltip(_ctip)
                    _state_badge(book)
                    if has_blocking_error(book):
                        # A hard error: files missing or corrupt/unreadable. Persisted actions are
                        # blocked, so flag it red with the specific cause in the tooltip.
                        ui.badge("Missing" if book.missing else "Error").props(
                            "color=negative"
                        ).tooltip(blocking_reason(book) or "Blocking problem")
                    if has_open_findings(book):
                        ui.icon("warning_amber", size="1.125rem").classes("text-warning").tooltip(
                            "Needs attention — see At a Glance"
                        )
                series = book.series[0].name if book.series else ""
                author = ", ".join(book.authors) or "unknown author"
                line2 = f"{author} · {series}" if series else author
                reason = weak_identity_reason(book)
                if reason is None:
                    ui.item_label(line2).props("caption")
                else:
                    field, prov = reason
                    with ui.row().classes("items-center no-wrap q-gutter-xs"):
                        ui.item_label(line2).props("caption")
                        ui.badge(controller.source_label(prov)).props("outline").classes(
                            "colophon-chip"
                        ).tooltip(f"{field.capitalize()}: {controller.source_tooltip(prov)}")
                chip_labels = book.genres + book.tags
                if chip_labels:
                    with ui.row().classes("items-center no-wrap q-gutter-xs q-mt-none"):
                        for label in chip_labels[:3]:
                            ui.chip(label, color=None).props(
                                "dense square size=sm clickable"
                            ).classes("colophon-tag-chip").tooltip(label).on(
                                "click.stop", lambda lbl=label: _filter_to(lbl)
                            )
                        rest = chip_labels[3:]
                        if rest:
                            ui.chip(f"+{len(rest)}", color=None).props(
                                "dense square size=sm"
                            ).classes("colophon-tag-chip colophon-tag-more").tooltip(
                                " · ".join(rest)
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

    def _on_detail_scroll(e) -> None:
        # Remember where the detail pane is scrolled so book navigation can restore it.
        _detail_view["scroll"] = e.vertical_position

    def _render_nav_more() -> None:
        pending = _nav_view["pending"]
        start = _nav_view["rendered"]
        end = min(start + _NAV_PAGE, len(pending))
        if start >= end or _nav_view["el"] is None:
            return
        with _nav_view["el"]:
            for render in pending[start:end]:
                render()
        _nav_view["rendered"] = end

    def _on_nav_scroll(e) -> None:
        if e.vertical_percentage > 0.85 and _nav_view["rendered"] < len(_nav_view["pending"]):
            _render_nav_more()

    def refresh_list() -> None:
        with span("list render"):
            _refresh_list()

    def _refresh_list() -> None:
        list_container.clear()
        row_elements.clear()
        if not controller.library_tree_warm():
            with list_container:
                skeleton_rows(8)
            _ensure_warm()
            return
        books = _visible_books()
        _list_view["books"] = books
        _list_view["rendered"] = 0
        _sync_deselect_btn()   # the visible set changed -> re-label deselect (visible vs all)
        with list_container:
            if not books:
                if book_filter["text"].strip():
                    with empty_state(
                        "search_off", "No books match your filter",
                        "Try a different term, or clear the filter to see everything in view.",
                    ):
                        pass
                else:
                    with empty_state(
                        "auto_stories", "No books here yet",
                        "Scan a folder and Colophon reads each audiobook's metadata, lines "
                        "them up, and lets you review before anything is written.",
                    ):
                        ui.button("Scan a folder", icon="radar", on_click=_do_scan).props(
                            "unelevated no-caps"
                        )
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
        repaint(list=True, nav=True, status=True)
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
            ui.label(
                "[ ... ] conditional groups are only for organize patterns, not parsing."
            ).classes("text-caption colophon-muted")
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
                repaint(nav=True, middle=True, status=True)
                if selected_ids:
                    _after_select()

            with ui.row().classes("w-full justify-end q-gutter-sm q-mt-md"):
                ui.button("Cancel", on_click=dialog.close).props("flat")
                apply_btn.on_click(_apply)

            _render_preview()
        dialog.open()

    def _reidentify_dialog() -> None:
        """Re-identify the selected books with a chosen filename pattern: clears their
        folder/filename-derived fields and re-derives them from the pattern. Manual edits and
        matched data are kept. A preview shows how the pattern parses each selection filename."""
        books = _selected_books()
        if not books:
            ui.notify("Select one or more books first")
            return
        initial = controller.ctx.config.filename_template or "$Author - $Title"

        with modal() as dialog, ui.card().classes("w-full").style("max-width: 720px"):
            ui.label("Re-identify from filename").classes("text-h6")
            ui.label(
                f"Re-reads {len(books)} selected book(s) with the pattern below. Your manual edits "
                "and matched data are kept."
            ).classes("text-caption colophon-muted")
            with ui.row().classes("items-center q-gutter-xs q-mt-xs"):
                ui.label("Fields:").classes("text-caption colophon-muted")
                for tok in PARSE_TOKENS:
                    badge = ui.badge(f"${tok.name}").props("color=grey-7 outline")
                    if tok.field is None:  # $Skip
                        badge.props("color=grey-5").tooltip("Matches and discards a run")

            pattern_input = ui.input("Pattern", value=initial).props(
                "dense clearable").classes("w-full q-mt-sm")
            attach_history_menu(
                pattern_input, controller.ctx.config.recent_filename_templates,
                lambda p: p, lambda p: pattern_input.set_value(p), tooltip="Recent patterns",
            )
            preview_box = ui.column().classes("w-full q-mt-sm")
            apply_btn = ui.button("Re-identify", icon="refresh")

            def _render_preview() -> None:
                preview_box.clear()
                pat = (pattern_input.value or "").strip()
                try:
                    compile_template(pat)
                except ValueError as e:
                    apply_btn.set_enabled(False)
                    with preview_box:
                        ui.label(f"Invalid pattern: {e}").classes("text-caption text-negative")
                    return
                apply_btn.set_enabled(True)
                matched = 0
                with preview_box:
                    with ui.scroll_area().classes("w-full").style("max-height: 32vh"):
                        with ui.list().props("dense").classes("w-full"):
                            for b in books:
                                parsed = controller.preview_filename_parse(b, pat)
                                if parsed:
                                    matched += 1
                                with ui.item(), ui.item_section():
                                    ui.item_label(controller.book_filename(b)).classes("ellipsis")
                                    if parsed:
                                        ui.item_label(
                                            ", ".join(f"{k}={v}" for k, v in parsed.items())
                                        ).props("caption")
                                    else:
                                        ui.item_label("no match").props("caption").classes(
                                            "colophon-muted")
                    ui.label(f"{matched} of {len(books)} filename(s) match").classes(
                        "text-caption colophon-muted")

            pattern_input.on_value_change(lambda _e: _render_preview())

            async def _apply() -> None:
                pat = (pattern_input.value or "").strip()
                try:
                    compile_template(pat)
                except ValueError as e:
                    ui.notify(f"Invalid pattern: {e}", type="negative")
                    return
                apply_btn.props("loading=true")
                try:
                    n = await asyncio.to_thread(controller.reidentify, books, template=pat)
                finally:
                    apply_btn.props(remove="loading")
                ui.notify(f"Re-identified {n} book(s)")
                dialog.close()
                repaint(nav=True, middle=True, status=True)

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
                    # on_click (scope navigation) from also firing. The menu's
                    # contents are built lazily on first open, not up front:
                    # with thousands of author/series rows, eagerly constructing
                    # every (almost never opened) menu dominated nav render time.
                    with ui.button(icon="more_vert").props(
                        'flat dense round size=sm aria-label="Node actions"'
                    ).tooltip("Node actions").on("click.stop", lambda: None):
                        node_menu = ui.menu()
                        built = {"done": False}

                        def _populate(_=None, m=node_menu, build=menu, built=built) -> None:
                            if built["done"]:
                                return
                            built["done"] = True
                            with m:
                                build()

                        node_menu.on("before-show", _populate)

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
                repaint(nav=True, middle=True)
            else:
                repaint(nav=True)

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
        if not controller.library_tree_warm():
            with nav_container:
                skeleton_rows(6)
            _ensure_warm()
            return
        full = controller.library_tree()
        conditions = parse_query(book_filter["text"])
        if conditions or folder_filter["path"] is not None:
            # Narrow the navigator to the same books the list shows (folder ∧ text), so the two
            # panels never disagree. `visible` is the shared match set; None means no active filter.
            visible = {b.id for b in full.all_books if _in_folder(b) and _matches_filter(b, conditions)}
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
            # The header items (All / Needs id / Needs attention / phase groups) are few and
            # render up front. The author/series/franchise rows can number in the thousands and
            # dominate render time, so each becomes a render closure in `nav_pending`, windowed
            # onto the scroll area exactly like the book list — a slice now, the rest on scroll.
            nav_pending: list = []
            with ui.list().props("dense").classes("w-full") as nav_list:
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
                    if _in_folder(b) and _matches_filter(b, conditions)
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
                        nav_pending.append(
                            lambda s=s, ids=ids: _nav_item(
                                s.name,
                                "collections_bookmark",
                                kind == "series" and key == s.name,
                                lambda n=s.name: _set_scope("series", n),
                                checkbox=_node_checkbox(ids),
                                menu=lambda n=s.name: _entity_menu("series", n, nav_aliases),
                            )
                        )
                elif view["group_by"] == "franchise":
                    for f in tree.franchises:
                        ids = [b.id for b in f.books if _in_folder(b)]
                        if not ids:
                            continue
                        nav_pending.append(
                            lambda f=f, ids=ids: _nav_item(
                                f.name,
                                "hub",
                                kind == "franchise" and key == f.name,
                                lambda n=f.name: _set_scope("franchise", n),
                                checkbox=_node_checkbox(ids),
                                menu=lambda n=f.name: _entity_menu("franchise", n, nav_aliases),
                            )
                        )
                else:
                    for author in tree.authors:
                        aids = list(dict.fromkeys(  # dedup: a book may be in two of this author's series
                            [b.id for s in author.series for b in s.books if _in_folder(b)]
                            + [b.id for b in author.standalone if _in_folder(b)]
                        ))
                        if not aids:
                            continue  # no books from this author in the current folder
                        nav_pending.append(
                            lambda author=author, aids=aids: _nav_item(
                                author.name,
                                "person",
                                kind == "author" and key == author.name,
                                lambda name=author.name: _set_scope("author", name),
                                checkbox=_node_checkbox(aids),
                                menu=lambda n=author.name: _entity_menu("author", n, nav_aliases),
                            )
                        )
                if conditions and view["group_by"] != "phase" and not nav_pending:
                    ui.label("No matches").classes("colophon-muted text-caption q-pa-sm")
            _nav_view["pending"] = nav_pending
            _nav_view["rendered"] = 0
            _nav_view["el"] = nav_list
            _render_nav_more()

    def _update_count() -> None:
        n = len(selected_ids)
        middle_count.text = f"{n} selected" if n else ""
        _sync_deselect_btn()

    def _set_filter(value: str | None) -> None:
        book_filter["text"] = value or ""
        repaint(list=True, nav=True)  # the filter is cross-panel: it narrows the navigator to the same books
        _persist_view()

    def _filter_to(label: str) -> None:
        """Filter the Books list to an exact genre/tag (clicked from a chip)."""
        book_filter["text"] = label
        search = refs.get("filter")
        if search is not None:
            search.set_value(label)
        repaint(list=True, nav=True)
        _render_filter_chips()
        _persist_view()

    def _apply_query(text: str) -> None:
        """Set the filter to `text`, driving the input, the panels, and the chips
        row from the one query string."""
        text = text.strip()
        book_filter["text"] = text
        search = refs.get("filter")
        if search is not None:
            search.set_value(text)  # fires on_value_change -> _set_filter (repaint + persist)
        else:
            _set_filter(text)
        _render_filter_chips()

    def _add_condition(field: str, value: str, *, negated: bool) -> None:
        """Append a builder-produced condition to the current query. Commas in the
        value become OR-alternatives (except for `any`, kept literal); `negated`
        prepends `-`."""
        token = build_token(field, value or "", negated=negated)
        if not token:
            return
        current = book_filter["text"].strip()
        _apply_query(f"{current} {token}" if current else token)

    def _remove_condition(idx: int) -> None:
        """Drop the condition at `idx` (from a chip's remove control) and rewrite the query."""
        conditions = parse_query(book_filter["text"])
        if 0 <= idx < len(conditions):
            del conditions[idx]
            _apply_query(format_query(conditions))

    def _render_filter_chips() -> None:
        """Repaint the removable chip per active condition below the filter input."""
        box = refs.get("filter_chips")
        if box is None:
            return
        box.clear()
        conditions = parse_query(book_filter["text"])
        with box:
            for i, cond in enumerate(conditions):
                value = " or ".join(cond.values)
                text = value if cond.field is None else f"{field_label(cond.field)}: {value}"
                if cond.negated:
                    text = f"not {text}"
                ui.chip(text, removable=True).props("dense outline").classes(
                    "colophon-chip"
                ).on("remove", lambda _e, idx=i: _remove_condition(idx))

    def _set_facet(name: str, value) -> None:
        view["facets"][name] = value
        _update_count()
        refresh_list()

    def _set_sort(value: str) -> None:
        view["sort"] = value or "none"
        _update_count()
        refresh_list()

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
        # Books toolbar: facet bar + free-text filter + selection controls.
        middle_toolbar.clear()
        with middle_toolbar:
            ui.label("Every book in the current scope.").classes("text-caption colophon-muted")
            with ui.row().classes("items-center w-full q-gutter-xs"):
                ui.select(
                    _STATE_FILTER_OPTIONS,
                    multiple=True, label="State", value=view["facets"]["state"],
                    on_change=lambda e: _set_facet("state", e.value),
                ).props("dense outlined options-dense").classes("col").style("min-width: 8.5rem")
                ui.select(
                    {"low": "<40", "mid": "40-74", "high": "≥75"},
                    multiple=True, label="Confidence", value=view["facets"]["confidence"],
                    on_change=lambda e: _set_facet("confidence", e.value),
                ).props("dense outlined options-dense").classes("col").style("min-width: 8.5rem")
                id_trust_select = ui.select(
                    {
                        "directory": "Folder (weak)",
                        "filename": "Filename (weak)",
                        "graphing": "Inferred (weak)",
                        "tag": "File tag",
                        "datafile": "Datafile",
                        "manual": "Edited",
                        "match": "Match",
                    },
                    multiple=True, label="ID Trust", value=view["facets"]["id_trust"],
                    on_change=lambda e: _set_facet("id_trust", e.value),
                ).props("dense outlined options-dense").classes("col").style("min-width: 8.5rem")
                with id_trust_select:
                    ui.tooltip(
                        "ID Trust reflects how the book's core identity, its author and "
                        "series, was determined. Everything else builds on it: a match is "
                        "searched using this identity, and manual edits assume it. When the "
                        "identity was only inferred from a folder or file name, the matched "
                        "and edited data resting on it is only as trustworthy as that guess."
                    ).classes("colophon-tip")
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
            with ui.row().classes("items-center q-gutter-md"):
                # Count over scope+folder (not the text search): the toolbar isn't rebuilt on
                # every keystroke, so a text-inclusive count would go stale. Scope/folder changes
                # do rebuild it, keeping this current.
                needs_work_n = (
                    sum(1 for b in _books_for_scope() if needs_human(b))
                    if controller.library_tree_warm() else 0
                )
                ui.checkbox(
                    f"Needs work ({needs_work_n})", value=view["facets"]["needs_work"],
                    on_change=lambda e: _set_facet("needs_work", e.value),
                ).props("dense").tooltip(
                    "Only books that are not yet finished: anything still in progress "
                    "(not Ready, Organized, Encoded, or Skipped)."
                )
                ui.checkbox(
                    "Attention", value=view["facets"]["findings"],
                    on_change=lambda e: _set_facet("findings", e.value),
                ).props("dense").tooltip(
                    "Only books with an unresolved structural finding — duplicates, mixed works, "
                    "or an unclear folder layout."
                )
                ui.checkbox(
                    "Blocking errors", value=view["facets"]["errors"],
                    on_change=lambda e: _set_facet("errors", e.value),
                ).props("dense").tooltip(
                    "Only books with a fault that blocks persisting — missing or corrupt files."
                )
            with ui.row().classes("items-center w-full no-wrap q-gutter-xs"):
                search = filter_input(
                    _set_filter,
                    placeholder="Filter title, author, series, narrator, or field:value",
                    value=book_filter["text"],
                    aria_label="Filter the library",
                ).classes("col")
                refs["filter"] = search  # so the "/" shortcut can focus it
                with ui.button(icon="add").props("flat dense round") as add_btn:
                    add_btn.classes("colophon-muted").tooltip("Add a field-scoped filter")
                    with ui.menu() as add_menu:
                        with ui.column().classes("q-pa-sm gap-2").style("min-width: 260px"):
                            ui.label("Add filter").classes("text-caption colophon-muted")
                            field_sel = ui.select(
                                {tok: lbl for tok, lbl in FIELDS}, value="title",
                            ).props("dense outlined options-dense").classes("w-full")
                            val_in = ui.input(placeholder="value, or a, b for either").props(
                                "dense outlined"
                            ).classes("w-full")
                            exclude = ui.checkbox("Exclude").props("dense").tooltip(
                                "Match books that do not have this value"
                            )

                            def _submit_condition() -> None:
                                _add_condition(
                                    field_sel.value, val_in.value, negated=bool(exclude.value)
                                )
                                val_in.set_value("")
                                exclude.set_value(False)
                                add_menu.close()

                            val_in.on("keydown.enter", _submit_condition)
                            ui.button("Add", on_click=_submit_condition).props(
                                "dense no-caps unelevated color=primary"
                            ).classes("w-full")
                with ui.icon("help_outline").classes("colophon-muted cursor-pointer"):
                    with ui.tooltip().classes("colophon-tip"):
                        ui.html(
                            "<b>Filter syntax</b><br>"
                            "Type any words to match across the book. Separate rules with "
                            "spaces; every rule must match.<br><br>"
                            "<code>author:sanderson</code> limits a word to one field.<br>"
                            "<code>author:sanderson,jordan</code> matches either value.<br>"
                            "<code>-narrator:kramer</code> excludes matches.<br>"
                            '<code>title:"way of kings"</code> quotes a multi-word value.'
                            "<br><br>"
                            "Fields: title, subtitle, author, narrator, series, franchise, "
                            "publisher, genre, tag, filename, asin, isbn, year, language, "
                            "description."
                        )
            filter_chips = ui.row().classes("items-center w-full q-gutter-xs")
            refs["filter_chips"] = filter_chips
            _render_filter_chips()
            with ui.row().classes("items-center w-full no-wrap q-gutter-xs"):
                ui.button("Select all", icon="done_all", on_click=_select_visible) \
                    .props("flat dense no-caps").tooltip("Select all books matching the filter")
                refs["deselect_btn"] = ui.button(
                    "Deselect visible", icon="remove_done", on_click=_deselect_smart
                ).props("flat dense no-caps").tooltip(
                    "Deselect the books matching the filter; once none are visible, deselects all "
                    "(including any hidden by the filter)"
                )
                _sync_deselect_btn()   # label for the current selection
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
        repaint(list=True, status=True)
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
                    ui.badge(f"{label} {count}").props(f"color={color}").tooltip(
                        state_description(BookState(state))
                    )
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

    def repaint(
        *, nav: bool = False, middle: bool = False, list: bool = False,
        status: bool = False, detail_book_id: str | None = None,
    ) -> None:
        """The single Library repaint path. Each flag rebuilds exactly one pane, so a
        mutation declares its blast radius and cannot silently skip a pane. Heavy panes
        rebuild through their existing (windowed) functions; a cold derivation renders a
        skeleton and derives off-thread (handled inside the list/nav refreshers)."""
        if nav:
            refresh_nav()
        if middle:
            _render_middle()
        if list:
            refresh_list()
        if status:
            refresh_status()
        if detail_book_id is not None:
            show_detail(detail_book_id)
        _stepper_refresh["fn"]()
        if detail_book_id is None and len(selected_ids) >= 2:
            show_bulk()

    def _rerun_notify(result: RerunResult) -> None:
        """Report a re-run and, crucially, which downstream phases were left stale."""
        msg = f"Re-ran {state_panel.phase_label(result.ran)} for {result.book_count} book(s)"
        if result.staled:
            names = ", ".join(state_panel.phase_label(p) for p in Phase if p in result.staled)
            msg += f" — {names} marked stale"
        ui.notify(msg)
        if result.failed:
            ui.notify(f"{result.failed} failed — see their timeline", type="negative")

    def _refresh_all() -> None:
        with span("refresh_all"):
            repaint(nav=True, middle=True, status=True)

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

    _warming = {"active": False}

    async def _warm_tree() -> None:
        """Derive library_tree off the event loop, then repaint the heavy panes and the
        toolbar count now that the cache is warm."""
        try:
            await asyncio.to_thread(controller.library_tree)  # under the store lock, off-loop
        finally:
            _warming["active"] = False
        _ui_safe(lambda: repaint(nav=True, middle=True))

    def _ensure_warm() -> None:
        """Schedule the warmer once if the tree is cold and not already warming.

        Uses background_tasks.create rather than ui.timer: a ui.timer is an Element
        bound to the slot active at creation time, and a cold repaint clears the very
        containers whose slot the timer would attach to — so the timer fires into a
        deleted slot ("The parent slot of the element has been deleted"), its body
        never runs, and `_warming` stays wedged True until a full page reload. A
        background task has no parent slot and always runs."""
        if not controller.library_tree_warm() and not _warming["active"]:
            _warming["active"] = True
            background_tasks.create(_warm_tree(), name="library-warm")

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

    def _review_weak_identity() -> None:
        # Land on all weakly-inferred books: clear existing filters, then set ID Trust to the three
        # weak tiers. _render_middle rebuilds the facet selects + text filter from this state, so a
        # middle repaint syncs the widgets and re-renders the list.
        view["facets"] = dict(FACET_DEFAULTS)
        view["facets"]["id_trust"] = list(WEAK_ID_TRUST_TIERS)
        book_filter["text"] = ""
        repaint(middle=True)

    async def _do_match() -> None:
        await match_dialog(
            controller, refresh_all=_refresh_all, selected_ids=set(selected_ids),
            on_review_weak=_review_weak_identity,
        )

    async def _do_persist() -> None:
        # Use the canonical _clear_selection (repaints + collapses the bulk panel), not the raw
        # set.clear — otherwise a persist that removes books leaves the "Editing N books" panel and
        # the selection UI stale until the next full repaint.
        await persist_dialog(controller, refresh_all=_refresh_all, selected_ids=set(selected_ids),
                             clear_selection=_clear_selection)

    with ui.header(elevated=True).classes("items-center q-px-md"):
        brand_mark()
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
            with ui.scroll_area(on_scroll=_on_nav_scroll).classes("col"):
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
            # Primary save actions live here, OUTSIDE the scroll area, so they stay
            # visible no matter how far the fields scroll. (position:sticky is unreliable
            # inside Quasar's QScrollArea + nested tab panels, so this is structural.)
            detail_actions = ui.row().classes(
                "colophon-actionbar w-full no-wrap items-center q-gutter-sm"
            )
            detail_scroll = ui.scroll_area(on_scroll=_on_detail_scroll).classes("col")
            with detail_scroll:
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
