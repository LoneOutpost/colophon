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

from nicegui import ui

from colophon.controller import AppController
from colophon.core.chapters import file_boundary_chapters
from colophon.core.fields import EDITABLE_FIELDS, field_provenance, get_field
from colophon.core.filename_parser import VALID_FILENAME_FIELDS, compile_template
from colophon.core.models import BookState, BookUnit
from colophon.core.normalize import FIELD_NORMALIZERS, NORMALIZABLE_FIELDS, normalize_text
from colophon.ui.tabs import app_tabs
from colophon.ui.theme import apply_theme, dark_mode_button, setup_dark_mode

logger = logging.getLogger(__name__)

# Sentinel marking a bulk-edit field whose selected books hold differing values.
_MIXED = object()

_PLACEHOLDER_RE = re.compile(r"%(\w+)%")


def _placeholder_fields(template: str) -> list[str]:
    """Placeholder names in `template`, in order, excluding %skip% and duplicates."""
    seen: list[str] = []
    for name in _PLACEHOLDER_RE.findall(template):
        if name != "skip" and name not in seen:
            seen.append(name)
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
_STATE_BADGE: dict[BookState, tuple[str, str]] = {
    BookState.DETECTED: ("Detected", "grey-6"),
    BookState.IDENTIFIED: ("Identified", "grey-6"),
    BookState.NEEDS_REVIEW: ("Review", "warning"),
    BookState.READY: ("Ready", "positive"),
    BookState.ENCODING: ("Encoding", "info"),
    BookState.ORGANIZED: ("Organized", "info"),
    BookState.FAILED: ("Failed", "negative"),
    BookState.SKIPPED: ("Skipped", "grey-6"),
}

# Status-bar state badges: (BookState value, short label, color). Shown only when count > 0.
_STATUS_BADGES = [
    ("detected", "Detected", "grey-6"),
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


def _fmt_runtime_delta(candidate_ms: int | None, book_ms: int) -> str:
    """'8h 12m · +6m' (delta vs the book) or '8h 12m' when the book length is
    unknown; '' when the candidate has no runtime."""
    if not candidate_ms:
        return ""
    base = _fmt_duration(candidate_ms / 1000)
    if not book_ms:
        return base
    delta_s = (candidate_ms - book_ms) / 1000
    sign = "+" if delta_s >= 0 else "-"
    return f"{base} · {sign}{_fmt_duration(abs(delta_s))}"


def _confidence_color(value: float) -> str:
    if value >= 75:
        return "positive"
    if value >= 40:
        return "warning"
    return "negative"


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


def _cover_src(book: BookUnit) -> str | None:
    """The cover-serving URL for a book, or None when it has no cover. The
    `?v=` cache-buster refreshes the image whenever the book changes."""
    if book.cover_path or book.cover_url:
        return f"/cover/{book.id}?v={int(book.updated_at.timestamp())}"
    return None


def _render_cover(book: BookUnit, *, width: int, height: int, icon: str = "") -> None:
    """Render a book's cover at its natural aspect ratio: the width is fixed and the
    height follows the image, so portrait (book) and square covers both show
    uncropped. `height` sizes the neutral placeholder box shown when there is no
    cover (kept book-shaped)."""
    src = _cover_src(book)
    if src:
        ui.image(src).classes("rounded").style(f"width:{width}px;height:auto")
    else:
        with ui.element("div").classes("flex items-center justify-center rounded").style(
            f"width:{width}px;height:{height}px;background:rgba(120,120,128,.15)"
        ):
            ui.icon("menu_book", color="grey-6").classes(icon)


def _short_location(folder: Path | None) -> str:
    """A compact display of a book's folder: the last two path segments joined by
    ' / ' (for example 'Sanderson / The Way of Kings'), or the final segment alone,
    or '' when there is no folder. The full path is shown in a tooltip by the
    caller."""
    if folder is None:
        return ""
    parts = [p for p in folder.parts if p not in ("/", "\\")]
    if not parts:
        return ""
    return " / ".join(parts[-2:])


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
        "<script>"
        "(function(){"
        "var MIN={nav:200,mid:320},MAX={nav:520,mid:960};"
        "var VAR={nav:'--colophon-nav-w',mid:'--colophon-mid-w'};"
        "var KEY={nav:'colophon.navW',mid:'colophon.midW'};"
        "function setW(key,px){document.documentElement.style.setProperty(VAR[key],px+'px');}"
        "['nav','mid'].forEach(function(k){"
        "var v=localStorage.getItem(KEY[k]);if(v)setW(k,parseInt(v,10));});"
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
        "setW(drag.key,w);drag.last=w;});"
        "document.addEventListener('pointerup',function(){"
        "if(!drag)return;if(drag.last)localStorage.setItem(KEY[drag.key],drag.last);"
        "drag=null;document.body.style.userSelect='';});"
        "window.colophonResetColumns=function(){"
        "['nav','mid'].forEach(function(k){"
        "localStorage.removeItem(KEY[k]);"
        "document.documentElement.style.removeProperty(VAR[k]);});};"
        "})();"
        "</script>"
    )
    dark = setup_dark_mode()
    selected_ids: set[str] = set()
    # `scope` is the author/series/all/needs_id selection; `folder_filter` is an
    # orthogonal, persistent constraint set by browsing a folder. Both the Books
    # list and the navigator (author/series list) respect the folder filter, and
    # a scope selection refines within it.
    scope: dict[str, object] = {"kind": "all", "key": None}
    folder_filter: dict[str, object] = {"path": None}
    foster_selected: set[Path] = set()
    book_filter: dict[str, str] = {"text": initial_filter or ""}
    editor_state: dict[str, object] = {
        "book_id": None, "is_dirty": None, "save_pending": None, "save": None, "write": None,
    }

    def _clear_editor_state() -> None:
        editor_state.update(
            book_id=None, is_dirty=None, save_pending=None, save=None, write=None
        )
        ui.run_javascript("window.__colophon_dirty = false")

    def _guard_nav(target_book_id, then) -> bool:
        """If a different dirty detail is open, prompt and return True (handled);
        else return False so the caller proceeds."""
        is_dirty = editor_state["is_dirty"]
        if is_dirty is None or not is_dirty() or editor_state["book_id"] == target_book_id:
            return False
        with ui.dialog() as d, ui.card().classes("w-96"):
            ui.label("Unsaved changes").classes("text-subtitle1")
            ui.label("This book has unsaved edits.").classes("text-caption text-grey-7")
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
        "mode": "library", "cwd": None, "multiselect": False, "group_by": "author",
    }

    def _scan_roots() -> list[Path]:
        return list(controller.ctx.config.scan_paths)

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
        if kind == "needs_id":
            books = list(tree.needs_id)
        elif kind == "author":
            node = next((a for a in tree.authors if a.name == key), None)
            books = [b for s in node.series for b in s.books] + node.standalone if node else []
        elif kind == "series" and key:
            books = [b for a in tree.authors for s in a.series if s.name == key for b in s.books]
        else:  # "all"
            books = list(tree.needs_id)
            for a in tree.authors:
                books += [b for s in a.series for b in s.books] + a.standalone
        # The folder filter applies on top of every scope selection.
        return [b for b in books if _in_folder(b)]

    def _matches_filter(book, terms: list[str]) -> bool:
        if not terms:
            return True
        hay = f"{book_haystack(book)} {controller.book_filename(book).lower()}"
        return all(term in hay for term in terms)

    def _visible_books() -> list:
        """Books in the current scope, narrowed by the free-text filter."""
        terms = book_filter["text"].lower().split()
        books = _books_for_scope()
        if not terms:
            return books
        return [b for b in books if _matches_filter(b, terms)]

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
                    ui.label("Select a book to see its details").classes("text-grey-6")
                return

            def _cover_dialog(b=book) -> None:
                with ui.dialog() as dialog, ui.card().classes("w-[28rem]"):
                    ui.label("Change cover").classes("text-subtitle1")

                    url_in = ui.input("Image URL").props("dense clearable").classes("w-full")

                    def _set_url() -> None:
                        value = (url_in.value or "").strip()
                        if not value:
                            ui.notify("Enter a URL")
                            return
                        controller.set_cover_url(b, value)
                        dialog.close()
                        ui.notify("Cover set")
                        show_detail(b.id)

                    ui.button("Set from URL", icon="link", on_click=_set_url).props("flat dense no-caps")
                    ui.separator()

                    async def _on_upload(e) -> None:
                        data = await e.file.read()
                        res = controller.set_cover_upload(b, data, e.file.name)
                        if not res.ok:
                            ui.notify(res.error or "Upload failed", type="warning")
                            return
                        dialog.close()
                        ui.notify("Cover uploaded")
                        show_detail(b.id)

                    ui.upload(on_upload=_on_upload, auto_upload=True).props(
                        'accept="image/*" flat'
                    ).classes("w-full")
                    ui.separator()

                    grid = ui.row().classes("w-full q-gutter-xs q-mt-sm")

                    async def _search() -> None:
                        grid.clear()
                        with grid:
                            ui.spinner()
                        cands = await controller.cover_candidates(b)
                        grid.clear()
                        if not cands:
                            with grid:
                                ui.label("No covers found").classes("text-grey-6")
                            return
                        with grid:
                            for url in cands[:12]:
                                ui.image(url).classes("cursor-pointer rounded").style(
                                    "width:80px;height:120px;object-fit:contain"
                                ).on(
                                    "click",
                                    lambda u=url: (
                                        controller.set_cover_url(b, u),
                                        dialog.close(),
                                        ui.notify("Cover set"),
                                        show_detail(b.id),
                                    ),
                                )

                    ui.button("Search Audible and others", icon="search", on_click=_search).props(
                        "flat dense no-caps"
                    )
                    with ui.row().classes("w-full justify-end q-mt-sm"):
                        ui.button("Cancel", on_click=dialog.close).props("flat")
                dialog.open()

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
                            ui.badge(controller.source_label(source)).props("color=grey-6 outline").classes("self-center")
                        return
                    if field == "description":
                        inp = ui.textarea(field, value=value).props("dense").classes("col")
                    else:
                        inp = ui.input(
                            field, value=value, autocomplete=autocomplete.get(field)
                        ).props("dense").classes("col")
                        if field in ("year", "asin"):
                            inp.classes("colophon-mono")
                    inputs[field] = inp
                    source = field_provenance(book, field)
                    if source:
                        ui.badge(controller.source_label(source)).props("color=grey-6 outline").classes("self-center")

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
                ui.run_javascript("window.__colophon_dirty = false")
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

            def _compare(b=book) -> None:
                field_labels = {
                    "title": "Title", "author": "Author", "narrator": "Narrator",
                    "series": "Series", "sequence": "Sequence", "year": "Year",
                    "asin": "ASIN", "description": "Description",
                }
                services = controller.available_sources()  # [(name, label), ...]
                service_label = dict(services)
                state = {
                    "title": get_field(b, "title") or "",
                    "author": get_field(b, "author") or "",
                    "series": get_field(b, "series") or "",
                    "asin": get_field(b, "asin") or "",
                    "service": services[0][0] if services else None,
                }
                matches: list = []

                with ui.dialog() as dialog, ui.card().classes("w-96"):
                    ui.label(f"Find matches for {b.title or '(untitled)'}").classes("text-subtitle1")
                    body = ui.column().classes("w-full")

                    def show_form() -> None:
                        body.clear()
                        with body:
                            if not services:
                                ui.label("No metadata sources configured.").classes("text-grey-6")
                                ui.button("Close", on_click=dialog.close).props("flat")
                                return
                            title_in = ui.input("Title", value=state["title"]).props("dense").classes("w-full")
                            author_in = ui.input("Author", value=state["author"]).props("dense").classes("w-full")
                            series_in = ui.input("Series", value=state["series"]).props("dense").classes("w-full")
                            asin_in = ui.input("ASIN", value=state["asin"]).props("dense").classes("w-full")
                            ui.label("Search with").classes("text-caption text-grey-7 q-mt-sm")
                            service_radio = ui.radio(dict(services), value=state["service"]).props("dense")

                            async def _go() -> None:
                                state.update(
                                    title=title_in.value, author=author_in.value,
                                    series=series_in.value, asin=asin_in.value,
                                    service=service_radio.value,
                                )
                                await run_search()

                            with ui.row().classes("w-full justify-end q-gutter-sm q-mt-sm"):
                                ui.button("Cancel", on_click=dialog.close).props("flat")
                                ui.button("Search", icon="search", on_click=_go)

                    def show_searching() -> None:
                        body.clear()
                        with body, ui.row().classes("items-center q-gutter-sm q-pa-md"):
                            ui.spinner()
                            ui.label(f"Searching {service_label.get(state['service'], '')}…")

                    async def run_search() -> None:
                        show_searching()
                        try:
                            results = await controller.search_matches(
                                b, title=state["title"], author=state["author"],
                                series=state["series"], asin=state["asin"],
                                source_name=state["service"],
                            )
                        except Exception:
                            logger.exception("search_matches failed")
                            results = []
                        matches.clear()
                        matches.extend(results)
                        show_candidates()

                    def show_candidates() -> None:
                        body.clear()
                        with body:
                            with ui.row().classes("items-center w-full no-wrap"):
                                ui.button(
                                    "Back to search", icon="arrow_back", on_click=show_form
                                ).props("flat dense no-caps")
                                ui.space()
                                ui.label(service_label.get(state["service"], "")).classes(
                                    "text-caption text-grey-6"
                                )
                            if not matches:
                                ui.label("No matches found").classes("text-grey-6 q-pa-sm")
                            with ui.list().props("dense").classes("w-full"):
                                for m in matches[:10]:
                                    authors = ", ".join(m.authors) or "unknown"
                                    year = f" ({m.publish_year})" if m.publish_year else ""
                                    with ui.item(on_click=lambda result=m: show_picker(result)).props("clickable"):
                                        with ui.item_section():
                                            ui.item_label(m.title or "?")
                                            ui.item_label(f"{controller.source_label(m.provider)} · {authors}{year}").props("caption")
                                            rt = _fmt_runtime_delta(m.runtime_ms, b.duration_ms)
                                            if rt or m.abridged is not None:
                                                with ui.row().classes("items-center no-wrap q-gutter-xs"):
                                                    if rt:
                                                        ui.item_label(rt).props("caption").classes("colophon-mono")
                                                    if m.abridged is not None:
                                                        ui.badge("Abridged" if m.abridged else "Unabridged").props("color=grey-6 outline")

                    def show_picker(result) -> None:
                        body.clear()
                        checks: dict[str, ui.checkbox] = {}
                        with body:
                            ui.button("Back to matches", icon="arrow_back", on_click=show_candidates).props(
                                "flat dense no-caps"
                            )
                            with ui.scroll_area().classes("w-full").style("max-height: 45vh"):
                                with ui.list().props("dense").classes("w-full"):
                                    for key, source in controller.match_field_values(result).items():
                                        current = get_field(b, key)
                                        with ui.item():
                                            with ui.item_section().props("avatar"):
                                                checks[key] = ui.checkbox(value=(source != (current or None)))
                                            with ui.item_section():
                                                ui.item_label(f"{field_labels.get(key, key)}: {source}")
                                                ui.item_label(f"current: {current or '(none)'}").props("caption")
                                    if result.cover_url:
                                        with ui.item():
                                            with ui.item_section().props("avatar"):
                                                checks["cover"] = ui.checkbox(value=(result.cover_url != b.cover_url))
                                            with ui.item_section():
                                                ui.item_label("Cover art")
                                                ui.item_label(result.cover_url).props("caption")

                            def _apply(res=result) -> None:
                                selected = {k for k, c in checks.items() if c.value}
                                if not selected:
                                    ui.notify("No fields selected")
                                    return
                                controller.apply_match_fields(b, res, selected)
                                dialog.close()
                                ui.notify(f"Applied {len(selected)} field(s) from {controller.source_label(res.provider)}")
                                refresh_list()
                                show_detail(b.id)

                            with ui.row().classes("w-full justify-end q-gutter-sm q-mt-sm"):
                                ui.button("Cancel", on_click=dialog.close).props("flat")
                                ui.button("Apply selected", icon="done_all", on_click=_apply)

                    show_form()
                dialog.open()

            async def _tag_dialog(b=book) -> None:
                _save_pending(b)  # "Write" encompasses Save: persist editor edits first
                plan = controller.tag_plan(b)
                with ui.dialog() as dialog, ui.card().classes("w-96"):
                    ui.label(f"Write tags to {len(plan.files)} file(s)").classes("text-subtitle1")
                    for warning in plan.warnings:
                        with ui.row().classes("items-center no-wrap"):
                            ui.icon("warning", color="warning")
                            ui.label(warning).classes("text-caption text-warning")
                    if plan.embed_cover:
                        ui.label("Cover art will be embedded.").classes("text-caption text-grey-7")
                    with ui.scroll_area().classes("w-full").style("max-height: 40vh"):
                        with ui.list().props("dense").classes("w-full"):
                            for fp in plan.files:
                                with ui.item():
                                    with ui.item_section():
                                        ui.item_label(fp.path.name)
                                        ui.item_label(", ".join(fp.changed_fields) or "no changes").props(
                                            "caption"
                                        )
                    actions = ui.row().classes("w-full justify-end q-gutter-sm q-mt-sm")
                    with actions:
                        ui.button("Cancel", on_click=dialog.close).props("flat")
                        commit_btn = ui.button("Write tags", icon="sell")

                    async def _commit() -> None:
                        commit_btn.props("loading=true")
                        try:
                            result = await controller.write_tags(b)
                        finally:
                            commit_btn.props(remove="loading")
                        actions.clear()
                        with actions:
                            note = f"Wrote {result.written} file(s)" + (
                                f", {result.failed} failed" if result.failed else ""
                            )
                            ui.label(note).classes("text-caption q-mr-auto self-center")
                            ui.button(
                                "Undo",
                                icon="undo",
                                on_click=lambda: (
                                    controller.undo_tag_batch(),
                                    ui.notify("Reverted tag write (embedded cover kept)"),
                                    dialog.close(),
                                ),
                            ).props("flat")
                            ui.button("Close", on_click=dialog.close).props("flat")
                        refresh_list()
                        refresh_status()

                    commit_btn.on_click(_commit)
                dialog.open()

            def _remap_dialog(b=book) -> None:
                with ui.dialog() as dialog, ui.card().classes("w-80"):
                    ui.label("Remap a field").classes("text-subtitle1")
                    ui.label("Move a field's value into another field (fixes mis-tagging).").classes(
                        "text-caption text-grey-6"
                    )
                    src = ui.select(list(EDITABLE_FIELDS), label="From", value="title").props("dense").classes("w-full")
                    dst = ui.select(list(EDITABLE_FIELDS), label="To", value="subtitle").props("dense").classes("w-full")
                    clear = ui.checkbox("Clear the source field after moving", value=True)

                    def _apply() -> None:
                        if src.value == dst.value:
                            ui.notify("Pick two different fields")
                            return
                        controller.remap(b, src=src.value, dst=dst.value, clear_source=clear.value)
                        dialog.close()
                        ui.notify(f"Remapped {src.value} to {dst.value}")
                        refresh_list()
                        show_detail(b.id)

                    with ui.row().classes("w-full justify-end q-gutter-sm q-mt-sm"):
                        ui.button("Cancel", on_click=dialog.close).props("flat")
                        ui.button("Remap", icon="swap_horiz", on_click=_apply)
                dialog.open()

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
                with ui.dialog() as dlg, ui.card().classes("w-80"):
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
                    ui.badge(f"{book.confidence:.0f}").props(f"color={_confidence_color(book.confidence)}")
                    _slabel, _scolor = _STATE_BADGE.get(book.state, (book.state.value, "grey-6"))
                    ui.badge(_slabel).props(f"color={_scolor} outline")
                    if book.source_folder is not None:
                        with ui.row().classes("items-center no-wrap q-gutter-xs").style("max-width:112px"):
                            ui.icon("folder", size="14px").classes("text-grey-6")
                            ui.label(_short_location(book.source_folder)).classes(
                                "text-caption text-grey-6 ellipsis"
                            ).tooltip(str(book.source_folder))
                # Main column: title, tools, grouped fields.
                with ui.column().classes("col q-gutter-none"):
                    ui.label(book.title or "(untitled)").classes("colophon-book-title text-h6")
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
                                ui.button("Matches", icon="search", on_click=_compare).props("flat dense no-caps").tooltip("Find and apply metadata matches")
                                ui.button("Chapters", icon="menu_book", on_click=_fetch_clicked).props("flat dense no-caps").tooltip("Fetch chapters from Audible")
                                ui.button("Cover", icon="image", on_click=_cover_dialog).props("flat dense no-caps").tooltip("Search or set the cover")
                        with ui.element("div").classes("colophon-toolgroup"):
                            ui.label("Clean up").classes("colophon-seccap")
                            with ui.row().classes("q-gutter-xs"):
                                ui.button("Normalize", icon="auto_fix_high", on_click=_normalize_all).props("flat dense no-caps").tooltip("Normalize all text fields")
                                ui.button("Remap", icon="swap_horiz", on_click=lambda b=book: _remap_dialog(b)).props("flat dense no-caps").tooltip("Move one field's value to another")

                    # --- grouped fields ---
                    ui.label("Identity").classes("colophon-seccap")
                    with ui.grid(columns=2).classes("w-full"):
                        for f in ("title", "subtitle", "author", "narrator", "series", "sequence"):
                            _build_field(f)
                    ui.label("Description").classes("colophon-seccap")
                    _build_field("description")
                    ui.label("Publication").classes("colophon-seccap")
                    with ui.grid(columns=2).classes("w-full"):
                        for f in ("year", "publisher", "language", "asin"):
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
                _inp.on_value_change(lambda _e=None: ui.run_javascript("window.__colophon_dirty = true"))

            with ui.row().classes("colophon-actionbar w-full no-wrap items-center q-gutter-sm"):
                ui.button("Save", icon="save", on_click=_save).props("unelevated")
                ui.button("Write tags", icon="sell", on_click=lambda b=book: _tag_dialog(b)).props("outline")
                ui.space()
                ui.button(
                    "Mark ready", icon="check",
                    on_click=lambda b=book: (controller.mark_ready(b), ui.notify("Marked ready"), refresh_list()),
                ).props("flat")

            editor_state.update(
                book_id=book_id, is_dirty=_is_dirty,
                save_pending=_save_pending, save=_save, write=lambda b=book: _tag_dialog(b),
            )
            ui.run_javascript("window.__colophon_dirty = false")

            if book.source_files:
                ui.separator().classes("q-my-sm")
                ui.label(f"Files ({len(book.source_files)})").classes("text-subtitle2")

                def _rename_dialog(sf_path: Path, b: BookUnit = book) -> None:
                    with ui.dialog() as dialog, ui.card():
                        ui.label("Rename file").classes("text-subtitle1")
                        name_input = ui.input("New filename", value=sf_path.name).classes("w-72")

                        def _do_rename() -> None:
                            if controller.rename_file(b, sf_path, name_input.value.strip()):
                                ui.notify("Renamed")
                            else:
                                ui.notify("Rename failed (name in use?)", type="negative")
                            dialog.close()
                            show_detail(b.id)

                        with ui.row():
                            ui.button("Rename", on_click=_do_rename)
                            ui.button("Cancel", on_click=dialog.close).props("flat")
                    dialog.open()

                with ui.list().props("dense bordered").classes("w-full"):
                    for idx, sf in enumerate(book.source_files):
                        with ui.item():
                            with ui.item_section():
                                ui.item_label(sf.path.name)
                                ui.item_label(_fmt_duration(sf.duration_seconds)).props("caption")
                            with ui.item_section().props("side"):
                                with ui.row().classes("q-gutter-xs no-wrap"):
                                    ui.button(icon="arrow_upward", on_click=lambda p=sf.path: (controller.move_file(book, p, -1), show_detail(book.id))).props("flat dense round").set_enabled(idx > 0)
                                    ui.button(icon="arrow_downward", on_click=lambda p=sf.path: (controller.move_file(book, p, 1), show_detail(book.id))).props("flat dense round").set_enabled(idx < len(book.source_files) - 1)
                                    ui.button(icon="edit", on_click=lambda p=sf.path: _rename_dialog(p)).props("flat dense round")
                                    ui.button(icon="remove_circle_outline", on_click=lambda p=sf.path: (controller.exclude_file(book, p), ui.notify("Excluded"), show_detail(book.id))).props("flat dense round color=negative")

                # chapters: applied named chapters (book.chapters) or file-boundary default
                applied = bool(book.chapters)
                chapters = book.chapters if applied else file_boundary_chapters(
                    [(sf.path.name, sf.duration_seconds) for sf in book.source_files]
                )
                with ui.row().classes("items-center w-full no-wrap q-mt-sm"):
                    ui.label(f"Chapters ({len(chapters)})").classes("text-subtitle2")
                    if applied:
                        ui.badge("from Audible").props("color=grey-6 outline").classes("self-center")
                    ui.space()
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
            ui.label("Blank fields are left unchanged.").classes("text-caption text-grey-6")
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
                refresh_list()
                refresh_status()
                show_bulk()

            async def _bulk_tag_dialog() -> None:
                _apply_pending_bulk()  # "Write" encompasses Save: apply pending edits first
                plans = [(b, controller.tag_plan(b)) for b in books]
                total_files = sum(len(p.files) for _, p in plans)
                with ui.dialog() as dialog, ui.card().classes("w-96"):
                    ui.label(f"Write tags to {len(books)} books ({total_files} files)").classes(
                        "text-subtitle1"
                    )
                    with ui.scroll_area().classes("w-full").style("max-height: 40vh"):
                        with ui.list().props("dense").classes("w-full"):
                            for b, plan in plans:
                                with ui.item():
                                    with ui.item_section():
                                        ui.item_label(b.title or "(untitled)")
                                        note = f"{len(plan.files)} file(s)" + (
                                            f" · {len(plan.warnings)} warning(s)" if plan.warnings else ""
                                        )
                                        ui.item_label(note).props("caption")
                    actions = ui.row().classes("w-full justify-end q-gutter-sm q-mt-sm")
                    with actions:
                        ui.button("Cancel", on_click=dialog.close).props("flat")
                        commit_btn = ui.button("Write tags", icon="sell")

                    async def _commit() -> None:
                        commit_btn.props("loading=true")
                        try:
                            results = await controller.write_tags_books(books)
                        finally:
                            commit_btn.props(remove="loading")
                        wrote = sum(r.written for r in results)
                        failed = sum(r.failed for r in results)
                        actions.clear()
                        with actions:
                            note = f"Wrote {wrote} file(s) across {len(books)} books" + (
                                f", {failed} failed" if failed else ""
                            )
                            ui.label(note).classes("text-caption q-mr-auto self-center")
                            ui.button(
                                "Undo",
                                icon="undo",
                                on_click=lambda: (
                                    controller.undo_tag_batch(),
                                    ui.notify("Reverted tag write (embedded covers kept)"),
                                    dialog.close(),
                                ),
                            ).props("flat")
                            ui.button("Close", on_click=dialog.close).props("flat")
                        refresh_list()
                        refresh_status()

                    commit_btn.on_click(_commit)
                dialog.open()

            ui.separator().classes("q-my-sm")
            with ui.row().classes("items-center w-full no-wrap q-gutter-sm"):
                ui.label("Normalize").classes("text-caption text-grey-7")
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
                                "handler": lambda b=batch: (controller.undo(b), show_bulk()),
                            }
                        ],
                    )
                    refresh_list()
                    refresh_status()
                    show_bulk()

                ui.button("Normalize", icon="auto_fix_high", on_click=_normalize).props("outline")

            async def _quick_match() -> None:
                sources = controller.available_sources()  # [(name, label), ...]
                with ui.dialog() as dialog, ui.card().classes("w-[32rem]"):
                    title = ui.label(f"Quick Match {len(books)} books").classes("text-subtitle1")
                    body = ui.column().classes("w-full")
                    proposals: list = []

                    def show_config() -> None:
                        body.clear()
                        title.set_text(f"Quick Match {len(books)} books")
                        checks: dict[str, ui.checkbox] = {}
                        field_checks: dict[str, ui.checkbox] = {}
                        with body:
                            ui.label("Search these sources").classes("text-caption text-grey-7")
                            for name, label in sources:
                                checks[name] = ui.checkbox(label, value=True).props("dense")
                            ui.label("Match using these fields").classes(
                                "text-caption text-grey-7 q-mt-sm"
                            )
                            for key, flabel in (
                                ("title", "Title"),
                                ("author", "Author"),
                                ("series", "Series"),
                                ("asin", "ASIN"),
                            ):
                                field_checks[key] = ui.checkbox(flabel, value=True).props("dense")
                            with ui.row().classes("w-full justify-end q-gutter-sm q-mt-sm"):
                                ui.button("Cancel", on_click=dialog.close).props("flat")

                                async def _search() -> None:
                                    chosen = [n for n, c in checks.items() if c.value]
                                    if not chosen:
                                        ui.notify("Select at least one source")
                                        return
                                    fields = {k for k, c in field_checks.items() if c.value}
                                    if not fields:
                                        ui.notify("Select at least one field to match on")
                                        return
                                    await run_search(chosen, fields)

                                ui.button("Search", icon="search", on_click=_search)

                    def show_searching() -> None:
                        body.clear()
                        with body, ui.row().classes("items-center q-gutter-sm q-pa-md"):
                            ui.spinner()
                            ui.label(f"Searching {len(books)} books…")

                    async def run_search(source_names: list[str], search_fields: set[str]) -> None:
                        show_searching()
                        found = await controller.quick_match_scan(books, source_names, search_fields)
                        proposals.clear()
                        proposals.extend(found)
                        show_preview()

                    def show_preview() -> None:
                        body.clear()
                        title.set_text(f"Quick Match {len(books)} books")
                        threshold = controller.review_threshold()
                        checks: dict[str, ui.checkbox] = {}
                        with body:
                            with ui.scroll_area().classes("w-full").style("max-height: 45vh"):
                                with ui.list().props("dense").classes("w-full"):
                                    for p in proposals:
                                        with ui.item():
                                            with ui.item_section().props("avatar"):
                                                if p.best is not None:
                                                    checks[p.book.id] = ui.checkbox(
                                                        value=p.confidence >= threshold
                                                    )
                                                else:
                                                    ui.icon("block").classes("text-grey-5")
                                            with ui.item_section():
                                                cur = p.book.title or "(untitled)"
                                                if p.best is not None:
                                                    prov = controller.source_label(p.best.provider)
                                                    ui.item_label(f"{cur} → {p.best.title or '?'}")
                                                    ui.item_label(prov).props("caption")
                                                else:
                                                    ui.item_label(cur)
                                                    ui.item_label("no match").props("caption")
                                            if p.best is not None:
                                                with ui.item_section().props("side"):
                                                    ui.badge(f"{p.confidence:.0f}").props(
                                                        f"color={_confidence_color(p.confidence)}"
                                                    )
                            with ui.row().classes("w-full justify-end q-gutter-sm q-mt-sm"):
                                ui.button("Cancel", on_click=dialog.close).props("flat")

                                def _apply() -> None:
                                    keep_ids = {bid for bid, c in checks.items() if c.value}
                                    chosen = [p for p in proposals if p.book.id in keep_ids]
                                    if not chosen:
                                        ui.notify("Nothing selected")
                                        return
                                    summary = controller.quick_match_apply(chosen)
                                    show_summary(summary)

                                ui.button("Apply selected", icon="done_all", on_click=_apply)

                    def show_summary(summary) -> None:
                        body.clear()
                        with body:
                            note = f"Applied {summary.applied_count} book(s), {summary.now_ready_count} now Ready"
                            ui.label(note).classes("text-body2 q-pa-sm")
                            with ui.row().classes("w-full justify-end q-gutter-sm q-mt-sm"):
                                if summary.batch_id:
                                    ui.button(
                                        "Undo", icon="undo",
                                        on_click=lambda b=summary.batch_id: (
                                            controller.undo(b),
                                            ui.notify("Reverted Quick Match"),
                                            _close(),
                                        ),
                                    ).props("flat")
                                ui.button("Close", on_click=_close)

                    def _close() -> None:
                        dialog.close()
                        refresh_nav()
                        refresh_list()
                        refresh_status()

                    show_config()
                dialog.open()

            with ui.row().classes("q-gutter-sm q-mt-sm"):
                ui.button("Quick Match", icon="auto_awesome", on_click=_quick_match).props("outline")

            with ui.row().classes("q-gutter-sm q-mt-sm"):
                ui.button("Apply to selection", icon="done_all", on_click=_apply_bulk)
                ui.button("Write tags", icon="sell", on_click=_bulk_tag_dialog).props("outline")
                ui.button(
                    "Clear selection",
                    icon="clear",
                    on_click=lambda: (
                        selected_ids.clear(),
                        refresh_list(),
                        refresh_status(),
                        show_detail(""),
                    ),
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

    def _deselect_all() -> None:
        selected_ids.clear()
        refresh_nav()
        refresh_list()
        refresh_status()
        _update_count()
        _after_select()

    def _select_visible() -> None:
        # Books-header "Select all": additive over the filtered, visible books.
        selected_ids.update(b.id for b in _visible_books())
        refresh_nav()
        refresh_list()
        refresh_status()
        _update_count()
        _after_select()

    def _deselect_visible() -> None:
        # Books-header "Deselect all": subtractive over the filtered, visible books;
        # selections outside the current filter are left untouched.
        selected_ids.difference_update(b.id for b in _visible_books())
        refresh_nav()
        refresh_list()
        refresh_status()
        _update_count()
        _after_select()

    def _toggle_book(book_id: str, on: bool) -> None:
        if on:
            selected_ids.add(book_id)
        else:
            selected_ids.discard(book_id)
        refresh_nav()  # keep navigator node checkboxes in sync
        refresh_status()
        _update_count()
        _after_select()

    def refresh_list() -> None:
        list_container.clear()
        row_elements.clear()
        books = _visible_books()
        with list_container:
            if not books:
                msg = (
                    "No books match the filter" if book_filter["text"].strip()
                    else "No books in this view"
                )
                ui.label(msg).classes("text-grey-6 q-pa-md")
                return
            # Every book is always individually selectable. The leading checkbox
            # toggles selection; clicking the title section opens the detail view.
            # Rows are keyboard-navigable; the focused row is tinted.
            with ui.list().props("separator dense").classes("w-full"):
                for book in books:
                    item = ui.item()
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
                            _render_cover(book, width=36, height=54)
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
                                        "text-caption text-grey-6 colophon-mono"
                                    )
                                ui.badge(f"{book.confidence:.0f}").props(
                                    f"color={_confidence_color(book.confidence)}"
                                )
                                _slabel, _scolor = _STATE_BADGE.get(
                                    book.state, (book.state.value, "grey-6")
                                )
                                ui.badge(_slabel).props(f"color={_scolor} outline")
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

    # --- keyboard navigation ---
    def _set_focus(book_id: str) -> None:
        """Focus a book row: tint it, open it in Details, and scroll it into view."""
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
        # Only drive the Books list in Library mode; NiceGUI's `ignore` list keeps
        # these keys from firing while a text field/button is focused.
        if view["mode"] != "library":
            return
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
        initial_pattern = controller.ctx.config.filename_template or "%author% - %title%"
        chosen: dict[str, bool] = {}

        with ui.dialog() as dialog, ui.card().classes("w-full").style("max-width: 720px"):
            ui.label("Parse from filename").classes("text-h6")
            ui.label(f"Applies to {len(books)} selected book(s).").classes(
                "text-caption text-grey-7"
            )

            # Field key: the placeholders that can appear in a pattern.
            with ui.row().classes("items-center q-gutter-xs q-mt-xs"):
                ui.label("Fields:").classes("text-caption text-grey-7")
                for name in sorted(VALID_FILENAME_FIELDS):
                    ui.badge(f"%{name}%").props("color=grey-7 outline")
                ui.badge("%skip%").props("color=grey-5 outline").tooltip(
                    "Matches and discards a segment"
                )

            pattern_input = ui.input("Pattern", value=initial_pattern).props(
                "dense clearable"
            ).classes("w-full q-mt-sm")

            saved_row = ui.row().classes("items-center w-full no-wrap q-gutter-xs q-mt-xs")
            fields_row = ui.row().classes("items-center w-full q-gutter-sm q-mt-sm")
            preview_box = ui.column().classes("w-full q-mt-sm")
            apply_btn = ui.button("Apply to selection", icon="auto_fix_high")

            def _render_saved() -> None:
                saved_row.clear()
                patterns = controller.ctx.config.saved_filename_patterns
                with saved_row:
                    if not patterns:
                        ui.label("No saved patterns yet").classes("text-caption text-grey-6")
                    for pat in patterns:
                        with ui.button(on_click=lambda p=pat: _load_pattern(p)).props(
                            "outline dense no-caps"
                        ).classes("q-pr-none"):
                            ui.label(pat).classes("text-caption")
                            ui.icon("close").classes("q-ml-xs").on(
                                "click.stop", lambda p=pat: _unsave(p)
                            ).tooltip("Remove this saved pattern")

            def _load_pattern(pat: str) -> None:
                pattern_input.set_value(pat)  # triggers _on_pattern_change -> preview

            def _unsave(pat: str) -> None:
                controller.remove_filename_pattern(pat)
                _render_saved()
                ui.notify("Removed saved pattern")

            def _save_current() -> None:
                pat = (pattern_input.value or "").strip()
                if not pat:
                    return
                try:
                    controller.save_filename_pattern(pat)
                except ValueError as e:
                    ui.notify(f"Invalid pattern: {e}", type="negative")
                    return
                _render_saved()
                ui.notify("Saved pattern")

            def _render_fields(present: list[str]) -> None:
                fields_row.clear()
                with fields_row:
                    if not present:
                        return
                    ui.label("Write:").classes("text-caption text-grey-7 self-center")
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
                # Fields the pattern can produce (in pattern order), intersected with valid ones.
                present = [
                    n for n in _placeholder_fields(pat) if n in VALID_FILENAME_FIELDS
                ]
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
                                                "text-grey-6"
                                            )
                                        elif effective:
                                            shown = ", ".join(f"{k}={v}" for k, v in effective.items())
                                            ui.item_label(shown).props("caption")
                                        else:
                                            ui.item_label("(no fields to write)").props(
                                                "caption"
                                            ).classes("text-grey-6")
                    ui.label(f"{matched} of {len(books)} filename(s) match").classes(
                        "text-caption text-grey-7"
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
                ui.button("Save pattern", icon="bookmark_add", on_click=_save_current).props("flat")
                ui.button("Cancel", on_click=dialog.close).props("flat")
                apply_btn.on_click(_apply)

            _render_saved()
            _render_preview()
        dialog.open()

    # --- folder browser (Folders mode) ---
    def _toggle_foster(path: Path, on: bool) -> None:
        if on:
            foster_selected.add(path)
        else:
            foster_selected.discard(path)

    def _foster_dialog() -> None:
        paths = sorted(foster_selected)
        if not paths:
            ui.notify("No files selected")
            return
        parents = {p.parent for p in paths}
        default_author = next(iter(parents)).name if len(parents) == 1 else ""
        with ui.dialog() as dialog, ui.card().classes("w-[28rem]"):
            ui.label(f"Organize {len(paths)} file(s) into books").classes("text-subtitle1")
            ui.label(
                "Each file moves into its own book folder, with author set from its "
                "containing folder and title from the filename. Optionally write the "
                "corrected tags into each file."
            ).classes("text-caption text-grey-6")
            override = ui.input("Override author for all", value="").props(
                "dense clearable"
            ).classes("w-full")
            if default_author:
                override.props(f'placeholder="{default_author}"')
            write_tags_cb = ui.checkbox("Write tags now", value=False)
            body = ui.column().classes("w-full")

            def _render_preview() -> None:
                body.clear()
                ovr = (override.value or "").strip() or None
                with body, ui.scroll_area().classes("w-full").style("max-height: 32vh"):
                    with ui.list().props("dense").classes("w-full"):
                        for p in paths:
                            author = ovr or p.parent.name
                            title = normalize_text(p.stem)
                            with ui.item(), ui.item_section():
                                ui.item_label(p.name)
                                ui.item_label(f"{author} · {title}").props("caption")

            override.on_value_change(lambda _e: _render_preview())
            _render_preview()
            actions = ui.row().classes("w-full justify-end q-gutter-sm q-mt-sm")
            with actions:
                ui.button("Cancel", on_click=dialog.close).props("flat")
                confirm = ui.button("Organize into books", icon="auto_awesome_motion")

            async def _commit() -> None:
                # Disk renames + field edits + optional tag write; run off the loop.
                confirm.props("loading=true")
                try:
                    result = await controller.restructure_as_books(
                        paths,
                        author_override=(override.value or "").strip() or None,
                        write_tags=write_tags_cb.value,
                    )
                finally:
                    confirm.props(remove="loading")
                foster_selected.clear()
                body.clear()
                with body:
                    note = f"Organized {result.fostered} of {len(paths)} into books"
                    if write_tags_cb.value:
                        note += f", retagged {result.retagged}"
                    ui.label(note).classes("text-body2")
                    if result.failures:
                        ui.label("Failed:").classes("text-caption text-negative q-mt-xs")
                        with ui.list().props("dense").classes("w-full"):
                            for r in result.failures:
                                with ui.item(), ui.item_section():
                                    ui.item_label(r.source.name)
                                    ui.item_label(r.error or "unknown error").props("caption")

                def _close_and_refresh() -> None:
                    # Refresh AFTER closing: _render_middle rebuilds the folder browser
                    # (which hosts this dialog's trigger), so refreshing while the dialog
                    # is open would tear it down before the result is read.
                    dialog.close()
                    refresh_nav()
                    _render_middle()

                actions.clear()
                with actions:
                    ui.button("Close", on_click=_close_and_refresh).props("flat")

            confirm.on_click(_commit)
        dialog.open()

    def refresh_folders() -> None:
        list_container.clear()
        roots = _scan_roots()
        cwd = view["cwd"]
        multi = len(roots) > 1
        if cwd is None and roots and not multi:
            cwd = roots[0]  # a single scan path is browsed directly
            view["cwd"] = cwd
        with list_container:
            if not roots:
                ui.label("No scan paths configured. Set them in Settings.").classes(
                    "text-grey-6 q-pa-md"
                )
                return
            if multi:
                # "All scan paths" is a neutral state (no combined view); the
                # operator picks one scan path to browse its folders.
                cwd_path = Path(str(cwd)) if cwd is not None else None
                selected = (
                    "__all__" if cwd_path is None
                    else next(
                        (str(r) for r in roots if cwd_path == r or r in cwd_path.parents),
                        "__all__",
                    )
                )
                options = {"__all__": "All scan paths"}
                options.update({str(r): (r.name or str(r)) for r in roots})
                ui.select(
                    options,
                    value=selected,
                    on_change=lambda e: _select_root(e.value),
                ).props("dense outlined").classes("w-full q-mb-sm")
            if cwd is None:
                with ui.column().classes("w-full items-center q-pa-lg q-gutter-sm"):
                    ui.icon("folder_open").classes("text-h4 text-grey-5")
                    ui.label("Pick a scan path above to browse its folders.").classes(
                        "text-grey-6 text-center"
                    )
                return
            cwd = Path(str(cwd))
            with ui.row().classes("items-center w-full no-wrap q-gutter-xs q-mb-xs"):
                ui.icon("folder_open").classes("text-grey-7")
                ui.label(str(cwd)).classes("text-caption text-grey-7 ellipsis col")
                ui.button(
                    "Organize into books", icon="subdirectory_arrow_right", on_click=_foster_dialog
                ).props("color=primary").tooltip(
                    "Move the selected loose files into a new book subfolder and retag them."
                )

            listing = controller.list_directory(cwd)
            with ui.list().props("dense bordered").classes("w-full"):
                # "Up" entry: hidden once cwd is a configured scan root, so normal
                # browsing stops at the root (nested roots are not specially handled).
                if cwd not in {Path(str(r)) for r in roots}:
                    with ui.item(on_click=lambda p=cwd.parent: _browse_to(p)).props("clickable"):
                        with ui.item_section().props("avatar"):
                            ui.icon("arrow_upward")
                        with ui.item_section():
                            ui.item_label("..")
                    ui.separator()
                if not listing.entries:
                    with ui.item():
                        with ui.item_section():
                            ui.item_label("(empty)").classes("text-grey-6")
                for entry in listing.entries:
                    if entry.is_dir:
                        with ui.item(on_click=lambda p=entry.path: _browse_to(p)).props("clickable"):
                            with ui.item_section().props("avatar"):
                                ui.icon("folder", color="amber-7")
                            with ui.item_section():
                                ui.item_label(entry.name)
                    elif entry.is_audio:
                        with ui.item():
                            with ui.item_section().props("avatar"):
                                ui.checkbox(
                                    value=entry.path in foster_selected,
                                    on_change=lambda e, p=entry.path: _toggle_foster(p, e.value),
                                )
                            with ui.item_section().props("avatar"):
                                ui.icon("audiotrack", color="primary")
                            with ui.item_section():
                                ui.item_label(entry.name)
                    else:
                        with ui.item().props("disable"):
                            with ui.item_section().props("avatar"):
                                ui.icon("insert_drive_file", color="grey-5")
                            with ui.item_section():
                                ui.item_label(entry.name).classes("text-grey-5")

    # --- navigator ---
    def _nav_item(
        label: str, icon: str, active: bool, on_click, color: str | None = None, *, checkbox=None
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

    def refresh_nav() -> None:
        nav_container.clear()
        tree = controller.library_tree()
        kind, key = scope["kind"], scope["key"]
        with nav_container:
            ui.toggle(
                {"library": "Library", "folders": "Folders"},
                value=view["mode"],
                on_change=lambda e: _set_mode(e.value),
            ).props("dense no-caps").classes("w-full q-mb-sm").tooltip(
                "Browse scan folders and organize loose files into their own book folders."
            )
            if view["mode"] == "folders":
                ui.label(
                    "Browse scan folders and foster loose files into their own subfolders."
                ).classes("text-caption text-grey-6")
                return
            ui.switch(
                "Multiselect", value=view["multiselect"], on_change=lambda e: _set_multiselect(e.value)
            ).props("dense").classes("q-mb-sm")
            ui.toggle(
                {"author": "By author", "series": "By series"},
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
                if view["group_by"] == "series":
                    series_books: dict[str, list[str]] = {}
                    for a in tree.authors:
                        for s in a.series:
                            ids = [b.id for b in s.books if _in_folder(b)]
                            if ids:
                                series_books.setdefault(s.name, []).extend(ids)
                    for name in sorted(series_books):
                        _nav_item(
                            name,
                            "collections_bookmark",
                            kind == "series" and key == name,
                            lambda n=name: _set_scope("series", n),
                            checkbox=_node_checkbox(series_books[name]),
                        )
                else:
                    for author in tree.authors:
                        aids = [
                            b.id for s in author.series for b in s.books if _in_folder(b)
                        ] + [b.id for b in author.standalone if _in_folder(b)]
                        if not aids:
                            continue  # no books from this author in the current folder
                        _nav_item(
                            author.name,
                            "person",
                            kind == "author" and key == author.name,
                            lambda name=author.name: _set_scope("author", name),
                            checkbox=_node_checkbox(aids),
                        )

    def _update_count() -> None:
        n = len(selected_ids)
        middle_count.text = f"{n} selected" if n else ""

    def _set_filter(value: str | None) -> None:
        book_filter["text"] = value or ""
        refresh_list()

    def _filter_to(label: str) -> None:
        """Filter the Books list to an exact genre/tag (clicked from a chip)."""
        book_filter["text"] = label
        search = refs.get("filter")
        if search is not None:
            search.set_value(label)
        refresh_list()

    def _render_middle() -> None:
        is_folders = view["mode"] == "folders"
        middle_title.text = "Folder contents" if is_folders else "Books"
        # Show a folder-filter indicator in the Books header (Library mode only).
        middle_filter.clear()
        if not is_folders and folder_filter["path"]:
            folder = Path(str(folder_filter["path"]))
            with middle_filter:
                ui.icon("filter_alt", color="primary")
                ui.label(f"Filtered to {folder.name or folder}").classes(
                    "text-caption text-primary ellipsis"
                ).tooltip(str(folder))
                ui.button(icon="close", on_click=_clear_folder_filter).props(
                    "flat dense round size=sm color=primary"
                ).tooltip("Clear folder filter")
        # Books toolbar: free-text filter + selection controls (Library mode only).
        middle_toolbar.clear()
        if not is_folders:
            with middle_toolbar:
                search = ui.input(
                    placeholder="Filter title, author, series, narrator, genre, tag, filename",
                    value=book_filter["text"],
                ).props("dense clearable debounce=300").classes("w-full")
                search.on_value_change(lambda e: _set_filter(e.value))
                refs["filter"] = search  # so the "/" shortcut can focus it

                def _clear_filter() -> None:
                    search.set_value("")
                    _set_filter("")
                    search.run_method("blur")  # return keyboard control to the list

                search.on("keydown.esc", _clear_filter)
                with search.add_slot("prepend"):
                    ui.icon("search")
                with ui.row().classes("items-center w-full no-wrap q-gutter-xs"):
                    ui.button("Select all", icon="done_all", on_click=_select_visible) \
                        .props("flat dense no-caps").tooltip("Select all books matching the filter")
                    ui.button("Deselect all", icon="remove_done", on_click=_deselect_visible) \
                        .props("flat dense no-caps").tooltip("Deselect the books matching the filter")
                    ui.space()
                    ui.button("Parse", icon="auto_fix_high", on_click=_parse_dialog) \
                        .props("flat dense no-caps").tooltip(
                            "Parse fields from the selected books' filenames"
                        )
        _update_count()
        if is_folders:
            refresh_folders()
        else:
            refresh_list()

    def _set_mode(mode: str) -> None:
        if _guard_nav(None, lambda: _set_mode(mode)):
            return
        _clear_editor_state()
        view["mode"] = mode
        refresh_nav()
        _render_middle()

    def _set_group_by(value: str) -> None:
        view["group_by"] = value
        scope["kind"], scope["key"] = "all", None  # reset scope when switching grouping
        refresh_nav()
        _render_middle()

    def _set_multiselect(on: bool) -> None:
        # The navigator's Multiselect switch only toggles author/series-node
        # checkboxes; per-book selection in the Books pane is always available, so
        # toggling it must not disturb the current selection.
        view["multiselect"] = on
        refresh_nav()

    def _set_scope(kind: str, key) -> None:
        scope["kind"], scope["key"] = kind, key
        refresh_nav()
        _render_middle()

    def _clear_folder_filter() -> None:
        folder_filter["path"] = None
        refresh_nav()  # author/series list returns to the full library
        _render_middle()

    def _browse_to(folder: Path) -> None:
        # Navigating the folder browser sets a folder filter that constrains both
        # the Books list and the navigator once you switch to Library mode. The
        # author/series selection resets so the whole folder is shown.
        view["cwd"] = folder
        folder_filter["path"] = str(folder)
        scope["kind"], scope["key"] = "all", None
        refresh_folders()

    def _select_root(value: str) -> None:
        if value == "__all__":
            folder_filter["path"] = None  # reset Library to all books
            scope["kind"], scope["key"] = "all", None
            view["cwd"] = None  # no scan path selected: show the pick-a-path prompt
            refresh_folders()
        else:
            _browse_to(Path(value))

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
            ui.icon("library_books").classes("text-grey-7")
            ui.label(f"{stats.get('total', 0)} books").classes("text-caption")
            for state, label, color in _STATUS_BADGES:
                count = stats.get(state, 0)
                if count:
                    ui.badge(f"{label} {count}").props(f"color={color}")
            ui.space()
            if selected_ids:
                ui.label(f"{len(selected_ids)} selected").classes("text-caption text-grey-7")
            ui.button("Undo", icon="undo", on_click=_undo).props("flat dense")

    def _refresh_all() -> None:
        refresh_nav()
        _render_middle()
        refresh_status()
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

    async def _scan() -> None:
        n = await asyncio.to_thread(controller.scan)
        ui.notify(f"Scanned {n} book units")

    async def _identify() -> None:
        await controller.identify_pending()

    async def _process() -> None:
        books = _selected_books() or controller.ready_books()
        if not books:
            ui.notify("Nothing selected or ready")
            return
        with ui.dialog() as dialog, ui.card().classes("w-96"):
            ui.label(f"Encode + organize {len(books)} book(s)").classes("text-subtitle1")
            statuses: dict[str, ui.item_label] = {}
            with ui.scroll_area().classes("w-full").style("max-height: 50vh"):
                with ui.list().props("dense").classes("w-full"):
                    for b in books:
                        with ui.item(), ui.item_section():
                            ui.item_label(b.title or "(untitled)")
                            statuses[b.id] = ui.item_label("pending").props("caption")
            summary = ui.row().classes("w-full items-center q-gutter-sm q-mt-sm")

            def _close() -> None:
                # Refresh the underlying views only on close — _render_middle rebuilds
                # the list panel, so refreshing while the dialog is open could disturb it.
                dialog.close()
                refresh_nav()
                _render_middle()
                refresh_status()

            async def _run_batch(targets: list) -> list:
                failed = []
                for b in targets:
                    statuses[b.id].set_text("working…")
                    await controller.ensure_cover_cached(b)
                    result = await asyncio.to_thread(controller.process_one, b, confirm_delete=False)
                    if result.organized:
                        statuses[b.id].set_text("organized")
                    else:
                        statuses[b.id].set_text(f"failed: {result.detail or 'see logs'}")
                        failed.append(b)
                return failed

            async def _go(targets: list) -> None:
                summary.clear()
                with summary:
                    ui.spinner(size="sm")
                    ui.label(f"Processing {len(targets)}...").classes("text-caption")
                failed = await _run_batch(targets)
                selected_ids.clear()
                await controller.trigger_abs_scan()  # best-effort library rescan
                summary.clear()
                with summary:
                    note = f"{len(targets) - len(failed)} organized" + (
                        f", {len(failed)} failed" if failed else ""
                    )
                    ui.label(note).classes("text-body2 q-mr-auto self-center")
                    if failed:
                        ui.button("Retry failed", icon="replay", on_click=lambda f=failed: _go(f))
                    ui.button("Close", on_click=_close).props("flat")

            dialog.open()
            await _go(books)

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
    ui.add_head_html(
        "<script>"
        "document.addEventListener('keydown', e => {"
        " if ((e.ctrlKey || e.metaKey) && (e.key === 's' || e.key === 'S')) e.preventDefault();"
        "}, true);"
        "window.__colophon_dirty = false;"
        "window.addEventListener('beforeunload', e => {"
        " if (window.__colophon_dirty) { e.preventDefault(); e.returnValue = ''; }"
        "});"
        "</script>"
    )

    with ui.header(elevated=True).classes("items-center q-px-md"):
        ui.icon("auto_stories", color="primary").classes("text-h5")
        ui.label("Colophon").classes("text-h6 q-ml-sm text-weight-medium")
        app_tabs(controller, "library")
        ui.space()
        scan_btn = ui.button("Scan", icon="search").props("flat")
        identify_btn = ui.button("Identify", icon="travel_explore").props("flat")
        process_btn = ui.button("Encode + organize", icon="play_arrow").props("unelevated")
        dark_mode_button(dark)
        ui.button(
            icon="view_column",
            on_click=lambda: ui.run_javascript("window.colophonResetColumns && colophonResetColumns()"),
        ).props("flat round").tooltip("Reset column widths")

    scan_btn.on_click(lambda: _run(scan_btn, _scan, "Scan complete"))
    identify_btn.on_click(lambda: _run(identify_btn, _identify, "Identification complete"))
    process_btn.on_click(_process)  # manages its own progress dialog + refresh

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
                middle_count = ui.label("").classes("text-caption text-grey-7")
            middle_toolbar = ui.column().classes("w-full gap-1 q-mt-xs")
            ui.separator().classes("q-mt-xs")
            with ui.scroll_area().classes("col"):
                list_container = ui.column().classes("w-full gap-0")
        ui.element("div").classes("colophon-resizer").tooltip("Drag to resize")
        with ui.card().classes("col column"):
            ui.label("Details").classes("text-subtitle1")
            ui.separator()
            with ui.scroll_area().classes("col"):
                detail_container = ui.column().classes("w-full gap-1")

    with ui.footer().classes("q-px-md q-py-xs"):
        status_container = ui.row().classes("items-center w-full no-wrap q-gutter-sm")

    _refresh_all()
    show_detail("")  # initial empty-state in the detail pane
