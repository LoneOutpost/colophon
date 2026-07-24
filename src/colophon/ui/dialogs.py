"""Dialog builders for the Library workspace, factored out of workspace.py.

Each builder is a standalone function taking the controller, the target book(s),
and explicit callbacks (refresh/show/clear) instead of closing over
render_workspace locals. `dialog_actions` and `busy` collapse the repeated
Cancel/confirm action row and the loading-button pattern.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable, Iterator
from contextlib import contextmanager
from pathlib import Path

from nicegui import ui

from colophon.adapters.lazylibrarian import PathPatterns
from colophon.controller import SCAN_DIFF_ROW_CAP, AppController
from colophon.core.chapters import Chapter, format_timecode, parse_timecode
from colophon.core.fields import EDITABLE_FIELDS, get_field
from colophon.core.models import BookState, BookUnit
from colophon.core.normalize import normalize_name
from colophon.core.pathscheme import sample_target
from colophon.core.phases import LOCAL
from colophon.core.sources import SourceResult
from colophon.core.triage import has_blocking_error, has_weak_identity, is_ready_to_persist
from colophon.services.editing import EMBEDDED_SOURCE_FIELDS
from colophon.services.ingest import ScanOptions, ScanScope
from colophon.ui.batch_log import BatchItem, BatchLog
from colophon.ui.scope import scope_selector

logger = logging.getLogger(__name__)


def _safe_ui(fn: Callable[[], object]) -> None:
    """Run a post-async UI update (a notify/refresh after an awaited action), swallowing the
    RuntimeError NiceGUI raises when the dialog or browser client has been torn down while the
    action ran (dialog closed, page navigated, or client disconnected). The action itself already
    completed; there is simply nothing live left to update."""
    try:
        fn()
    except RuntimeError:
        logger.info("skipped a dialog UI update; the dialog/client context is gone")

# Scan-dialog "depth" choice -> scan scope. UPDATE = add new + re-process changed/stale;
# REFRESH = force a full re-derive of everything in scope (the heal path).
_DEPTH_TO_SCOPE = {"new_changed": ScanScope.UPDATE, "rebuild": ScanScope.REFRESH}


def modal() -> ui.dialog:
    """A persistent dialog: it will not dismiss on an outside click or the Escape key, so unsaved
    edits are never lost to a stray click. The single place the app's dialog-dismissal policy lives;
    build every dialog through this rather than calling `ui.dialog()` directly."""
    return ui.dialog().props("persistent")


def dialog_actions(
    dialog: ui.dialog,
    *,
    confirm_label: str,
    confirm_icon: str,
    on_confirm: Callable[[], object],
    confirm_props: str = "unelevated",
) -> ui.button:
    """The standard right-aligned Cancel + confirm action row. Cancel closes the
    dialog; the confirm button is returned so callers can disable/busy it."""
    with ui.row().classes("w-full justify-end q-gutter-sm q-mt-sm"):
        ui.button("Cancel", on_click=dialog.close).props("flat")
        return ui.button(confirm_label, icon=confirm_icon, on_click=on_confirm).props(confirm_props)


@contextmanager
def busy(button: ui.button) -> Iterator[None]:
    """Show a button's spinner AND disable it for the duration of an action, always restoring it
    (replaces the hand-rolled props('loading=true') ... finally remove pattern). Disabling — not
    just the spinner — is what stops a second click landing while the action is in flight."""
    button.props("loading=true").disable()
    try:
        yield
    finally:
        button.props(remove="loading").enable()


def single_flight(handler: Callable[[], Awaitable[object]]) -> Callable[[], Awaitable[None]]:
    """Wrap an async click handler so it runs at most once at a time: a re-entrant click while a
    prior run is still awaiting is ignored. Belt-and-suspenders with `busy` (which disables the
    button): this closes the render-timing gap where a very fast second click is dispatched before
    the browser has applied the disabled state, so an action like writing tags can't run twice."""
    running = {"active": False}

    async def _wrapped() -> None:
        if running["active"]:
            return
        running["active"] = True
        try:
            await handler()
        finally:
            running["active"] = False

    return _wrapped


def attach_history_menu(
    field: ui.input,
    items: list,
    item_text: Callable[[object], object],
    on_pick: Callable[[object], None],
    *,
    tooltip: str = "Recent patterns",
) -> None:
    """Attach a 'recent values' dropdown to a text input's append slot; picking an
    entry calls on_pick with that item. Renders nothing when the history is empty."""
    if not items:
        return
    with field.add_slot("append"):
        with ui.button(icon="history").props("flat dense round size=sm").tooltip(tooltip):
            with ui.menu():
                for it in items:
                    ui.menu_item(str(item_text(it)), on_click=lambda i=it: on_pick(i))


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


def _fmt_series_label(name: str | None, sequence: float | None) -> str:
    """'Stormlight #1' / 'Stormlight #2.5' / 'Stormlight' (no seq) / '' (no name).
    The sequence drops a trailing '.0' so whole numbers read as integers."""
    if not name:
        return ""
    if sequence is None:
        return name
    seq = int(sequence) if sequence == int(sequence) else sequence
    return f"{name} #{seq}"


def _confidence_color(value: float) -> str:
    if value >= 75:
        return "positive"
    if value >= 40:
        return "warning"
    return "negative"


def _cover_thumb(url: str | None) -> None:
    """A small cover thumbnail for a match row; a placeholder when there's no art,
    so every row keeps the same left gutter."""
    if url:
        ui.image(url).classes("rounded").style(
            "width: 36px; height: 54px; object-fit: cover"
        )
    else:
        with ui.element("div").classes(
            "rounded flex items-center justify-center colophon-chip"
        ).style("width: 36px; height: 54px; border: 1px solid var(--colophon-border)"):
            ui.icon("menu_book", size="1.125rem").classes("colophon-muted")


def _clean_authors(authors: list[str]) -> list[str]:
    return [a.strip() for a in authors if a and a.strip()]


def authors_align(current: list[str], matched: list[str]) -> bool:
    """Whether a candidate's authors are consistent with the book's current authors.

    True when the two normalized author sets are equal, and also when either side is
    empty (no baseline to contradict), so a match is only flagged as a wrong book when
    both sides name an author and they disagree. Normalization matches the rest of the
    app (`normalize_name`, casefolded), so case/spacing/order differences still align."""
    cur = {normalize_name(a).casefold() for a in _clean_authors(current)}
    mat = {normalize_name(a).casefold() for a in _clean_authors(matched)}
    if not cur or not mat:
        return True
    return cur == mat


def _render_author_line(current: list[str], matched: list[str]) -> None:
    """Show the book's current author against the candidate's, so a wrong-book match is
    obvious. A green check when they align (or there's nothing to contradict); a warning
    when they name different authors."""
    cur = ", ".join(_clean_authors(current))
    mat = ", ".join(_clean_authors(matched)) or "unknown"
    aligned = authors_align(current, matched)
    with ui.row().classes("items-center no-wrap q-gutter-xs"):
        ui.icon("check_circle" if aligned else "warning", size="1rem",
                color="positive" if aligned else "warning")
        if not cur:
            text = f"Author: (none) → {mat}"     # no current author to compare against
        elif aligned:
            text = f"Author: {mat}"              # current and match agree
        else:
            text = f"Author: {cur} → {mat}"      # different author: likely a different book
        ui.label(text).classes("text-caption colophon-muted")


def _candidate_meta(result: SourceResult, book: BookUnit, *, source_label: str) -> None:
    """Render a candidate's metadata block (author comparison, captions, runtime/abridged
    row), comparing author + runtime against `book`. Emits NiceGUI elements into the current
    layout context; the caller owns any surrounding row/checkbox/expansion. Empty fields are
    omitted. Text uses the theme-aware `colophon-muted` token (not Quasar's `caption` prop,
    whose fixed light-theme colour reads as black on the dark dialog surface)."""
    _render_author_line(book.authors, result.authors)

    year = f" ({result.publish_year})" if result.publish_year else ""
    ui.label(f"{source_label}{year}").classes("text-caption colophon-muted")

    if result.narrators:
        ui.label(f"Narr: {', '.join(result.narrators)}").classes("text-caption colophon-muted")

    series = _fmt_series_label(result.series_name, result.series_sequence)
    pub_bits = [bit for bit in (series, result.publisher) if bit]
    if pub_bits:
        ui.label(" · ".join(pub_bits)).classes("text-caption colophon-muted")

    rt = _fmt_runtime_delta(result.runtime_ms, book.duration_ms)
    if rt or result.abridged is not None:
        with ui.row().classes("items-center no-wrap q-gutter-xs"):
            if rt:
                ui.label(rt).classes("text-caption colophon-mono colophon-muted")
            if result.abridged is not None:
                ui.badge("Abridged" if result.abridged else "Unabridged").props("outline").classes("colophon-chip")


_MOVE_HINT = "Move a field's value into another field (fixes mis-tagging)."
_SWAP_HINT = "Exchange the two fields' values."
_EMBEDDED_HINT = "Copy what the file's own tags carry into this field (move only)."


def _remap_from_options() -> dict[str, str]:
    """The Remap 'From' choices: the editable book fields, plus each embedded-tag source
    (namespaced 'embedded:<key>' so applying can tell them apart from a book field)."""
    options = {f: f for f in EDITABLE_FIELDS}
    options.update({f"embedded:{k}": f"embedded {k}" for k in EMBEDDED_SOURCE_FIELDS})
    return options


def _embedded_src(value: str) -> str | None:
    """The embedded-tag key when `value` is an embedded source, else None (a book field)."""
    return value.split(":", 1)[1] if value.startswith("embedded:") else None


def remap_dialog(
    controller: AppController,
    book: BookUnit,
    *,
    refresh_list: Callable[[], None],
    show_detail: Callable[[str], None],
) -> None:
    """Move one field's value into another field, or swap the two (fixes mis-tagging). An embedded
    tag can be a move-only source (copy what the file carries into a field)."""
    with modal() as dialog, ui.card().classes("w-80"):
        ui.label("Remap a field").classes("text-subtitle1")
        desc = ui.label(_MOVE_HINT).classes("text-caption colophon-muted")
        mode = ui.toggle({"move": "Move", "swap": "Swap"}, value="move").props("dense no-caps")
        src = ui.select(_remap_from_options(), label="From", value="title").props("dense").classes("w-full")
        dst = ui.select(list(EDITABLE_FIELDS), label="To", value="subtitle").props("dense").classes("w-full")
        clear = ui.checkbox("Clear the source field after moving", value=True)

        def _sync() -> None:
            embedded = _embedded_src(str(src.value)) is not None
            if embedded:
                mode.set_value("move")   # embedded is a one-way, move-only source
            mode.set_visibility(not embedded)
            swapping = mode.value == "swap"
            clear.set_visibility(not embedded and not swapping)
            desc.set_text(_EMBEDDED_HINT if embedded else (_SWAP_HINT if swapping else _MOVE_HINT))

        mode.on_value_change(lambda _e: _sync())
        src.on_value_change(lambda _e: _sync())

        def _apply() -> None:
            tag = _embedded_src(str(src.value))
            if tag is not None:
                if controller.remap_embedded(book, tag=tag, dst=dst.value) is None:
                    ui.notify(f"This file has no embedded {tag}")
                    return
                ui.notify(f"Moved embedded {tag} to {dst.value}")
            elif src.value == dst.value:
                ui.notify("Pick two different fields")
                return
            elif mode.value == "swap":
                controller.swap(book, field_a=src.value, field_b=dst.value)
                ui.notify(f"Swapped {src.value} and {dst.value}")
            else:
                controller.remap(book, src=src.value, dst=dst.value, clear_source=clear.value)
                ui.notify(f"Moved {src.value} to {dst.value}")
            dialog.close()
            refresh_list()
            show_detail(book.id)

        dialog_actions(dialog, confirm_label="Apply", confirm_icon="swap_horiz", on_confirm=_apply)
    dialog.open()


def bulk_remap_dialog(
    controller: AppController,
    books: list[BookUnit],
    *,
    clear_selection: Callable[[], None],
) -> None:
    """Move one field's value into another, or swap the two, across all selected books. An embedded
    tag can be a move-only source (copy each book's own file tag into a field)."""
    n = len(books)
    with modal() as dialog, ui.card().classes("w-80"):
        ui.label("Remap a field").classes("text-subtitle1")
        desc = ui.label(
            f"Move a field's value into another across {n} selected book(s)."
        ).classes("text-caption colophon-muted")
        mode = ui.toggle({"move": "Move", "swap": "Swap"}, value="move").props("dense no-caps")
        src = ui.select(_remap_from_options(), label="From", value="title").props("dense").classes("w-full")
        dst = ui.select(list(EDITABLE_FIELDS), label="To", value="subtitle").props("dense").classes("w-full")
        clear = ui.checkbox("Clear the source field after moving", value=True)

        def _sync() -> None:
            embedded = _embedded_src(str(src.value)) is not None
            if embedded:
                mode.set_value("move")
            mode.set_visibility(not embedded)
            swapping = mode.value == "swap"
            clear.set_visibility(not embedded and not swapping)
            if embedded:
                desc.set_text(f"Copy each book's own embedded tag into a field, across {n} book(s).")
            elif swapping:
                desc.set_text(f"Exchange the two fields' values across {n} selected book(s).")
            else:
                desc.set_text(f"Move a field's value into another across {n} selected book(s).")

        mode.on_value_change(lambda _e: _sync())
        src.on_value_change(lambda _e: _sync())

        def _apply() -> None:
            tag = _embedded_src(str(src.value))
            if tag is not None:
                controller.bulk_remap_embedded(books, tag=tag, dst=dst.value)
                ui.notify(f"Moved embedded {tag} to {dst.value} for {n} book(s)")
            elif src.value == dst.value:
                ui.notify("Pick two different fields")
                return
            elif mode.value == "swap":
                controller.bulk_swap(books, field_a=src.value, field_b=dst.value)
                ui.notify(f"Swapped {src.value} and {dst.value} for {n} book(s)")
            else:
                controller.bulk_remap(books, src=src.value, dst=dst.value, clear_source=clear.value)
                ui.notify(f"Moved {src.value} to {dst.value} for {n} book(s)")
            dialog.close()
            clear_selection()

        dialog_actions(dialog, confirm_label="Apply", confirm_icon="swap_horiz", on_confirm=_apply)
    dialog.open()


def rename_dialog(
    controller: AppController,
    book: BookUnit,
    sf_path: Path,
    *,
    show_detail: Callable[[str], None],
) -> None:
    """Rename a single source file of the book."""
    with modal() as dialog, ui.card():
        ui.label("Rename file").classes("text-subtitle1")
        name_input = ui.input("New filename", value=sf_path.name).classes("w-72")

        def _do_rename() -> None:
            if controller.rename_file(book, sf_path, name_input.value.strip()):
                ui.notify("Renamed")
            else:
                ui.notify("Rename failed (name in use?)", type="negative")
            dialog.close()
            show_detail(book.id)

        dialog_actions(dialog, confirm_label="Rename", confirm_icon="edit", on_confirm=_do_rename)
    dialog.open()


def cover_dialog(
    controller: AppController,
    book: BookUnit,
    *,
    show_detail: Callable[[str], None],
) -> None:
    """Set the book's cover from a URL, an upload, or a source search result."""
    with modal() as dialog, ui.card().classes("w-[28rem]"):
        ui.label("Change cover").classes("text-subtitle1")

        url_in = ui.input("Image URL").props("dense clearable").classes("w-full")

        def _set_url() -> None:
            value = (url_in.value or "").strip()
            if not value:
                ui.notify("Enter a URL")
                return
            controller.set_cover_url(book, value)
            dialog.close()
            ui.notify("Cover set")
            show_detail(book.id)

        ui.button("Set from URL", icon="link", on_click=_set_url).props("flat dense no-caps")
        ui.separator()

        async def _on_upload(e) -> None:
            data = await e.file.read()
            res = controller.set_cover_upload(book, data, e.file.name)
            if not res.ok:
                ui.notify(res.error or "Upload failed", type="warning")
                return
            dialog.close()
            ui.notify("Cover uploaded")
            show_detail(book.id)

        ui.upload(on_upload=_on_upload, auto_upload=True).props(
            'accept="image/*" flat'
        ).classes("w-full")
        ui.separator()

        grid = ui.row().classes("w-full q-gutter-xs q-mt-sm")

        async def _search() -> None:
            grid.clear()
            with grid:
                ui.spinner()
            cands = await controller.cover_candidates(book)
            grid.clear()
            if not cands:
                with grid:
                    ui.label("No covers found").classes("colophon-muted")
                return
            with grid:
                for url in cands[:12]:
                    ui.image(url).classes("cursor-pointer rounded").style(
                        "width:80px;height:120px;object-fit:contain"
                    ).on(
                        "click",
                        lambda u=url: (
                            controller.set_cover_url(book, u),
                            dialog.close(),
                            ui.notify("Cover set"),
                            show_detail(book.id),
                        ),
                    )

        ui.button("Search Audible and others", icon="search", on_click=_search).props(
            "flat dense no-caps"
        )
        with ui.row().classes("w-full justify-end q-mt-sm"):
            ui.button("Cancel", on_click=dialog.close).props("flat")
    dialog.open()


def chapter_edit_dialog(
    controller: AppController,
    book: BookUnit,
    chapters: list[Chapter],
    *,
    show_detail: Callable[[str], None],
) -> None:
    """Edit chapter titles and start times, optionally shifting them all by an
    offset; Save persists the timeline (sorted, ends recomputed) onto the book."""
    rows: list[dict] = []  # {"title": ui.input, "time": ui.input}

    def _read_time(row: dict) -> int | None:
        try:
            return parse_timecode(row["time"].value or "0")
        except ValueError:
            ui.notify(f"Bad time: {row['time'].value!r} (use H:MM:SS)", type="negative")
            return None

    with modal() as dialog, ui.card().classes("w-full").style("max-width: 640px"):
        ui.label(f"Edit chapters ({len(chapters)})").classes("text-subtitle1")
        ui.label(
            "Titles and start times are written into the M4B when you encode."
        ).classes("text-caption colophon-muted")

        with ui.row().classes("items-center no-wrap q-gutter-sm q-mt-xs"):
            shift_in = ui.number("Shift all (seconds)", value=0, format="%d").props(
                "dense"
            ).classes("w-40")

            def _apply_shift() -> None:
                delta_ms = round(float(shift_in.value or 0) * 1000)
                if not delta_ms:
                    return
                bases = [_read_time(row) for row in rows]
                if any(b is None for b in bases):
                    return
                for row, base in zip(rows, bases, strict=True):
                    row["time"].set_value(format_timecode(max(0, base + delta_ms)))
                shift_in.set_value(0)

            ui.button("Apply shift", icon="schedule", on_click=_apply_shift).props(
                "flat dense no-caps"
            )

        with ui.scroll_area().classes("w-full").style("max-height: 50vh"), \
                ui.list().props("dense").classes("w-full"):
            for n, ch in enumerate(chapters, start=1):
                with ui.item(), ui.row().classes("items-center w-full no-wrap q-gutter-sm"):
                    ui.label(str(n)).classes("colophon-muted").style("min-width: 1.5rem")
                    title_in = ui.input(value=ch.title).props("dense").classes("col")
                    time_in = ui.input(value=format_timecode(ch.start_ms)).props(
                        "dense"
                    ).classes("w-28")
                    rows.append({"title": title_in, "time": time_in})

        def _save() -> None:
            edited: list[Chapter] = []
            for row in rows:
                start = _read_time(row)
                if start is None:
                    return
                edited.append(
                    Chapter(title=(row["title"].value or "").strip(), start_ms=start, end_ms=start)
                )
            controller.save_chapters(book, edited)
            dialog.close()
            ui.notify(f"Saved {len(edited)} chapter(s)")
            show_detail(book.id)

        dialog_actions(dialog, confirm_label="Save", confirm_icon="save", on_confirm=_save)
    dialog.open()


def compare_dialog(
    controller: AppController,
    book: BookUnit,
    *,
    show_detail: Callable[[str], None],
    refresh_list: Callable[[], None],
) -> None:
    """Search metadata sources for the book and apply selected fields from a match."""
    field_labels = {
        "title": "Title", "author": "Author", "narrator": "Narrator",
        "series": "Series", "sequence": "Sequence", "year": "Year",
        "asin": "ASIN", "isbn": "ISBN", "description": "Description",
    }
    services = controller.available_sources()  # [(name, label), ...]
    service_label = dict(services)
    state = {
        "title": get_field(book, "title") or "",
        "author": get_field(book, "author") or "",
        "series": get_field(book, "series") or "",
        "asin": get_field(book, "asin") or "",
        "isbn": get_field(book, "isbn") or "",
        "service": services[0][0] if services else None,
    }
    matches: list = []

    with modal() as dialog, ui.card().classes("w-96"):
        ui.label(f"Find matches for {book.title or '(untitled)'}").classes("text-subtitle1")
        body = ui.column().classes("w-full")

        def show_form() -> None:
            body.clear()
            with body:
                if not services:
                    ui.label("No metadata sources configured.").classes("colophon-muted")
                    ui.button("Close", on_click=dialog.close).props("flat")
                    return
                title_in = ui.input("Title", value=state["title"]).props("dense").classes("w-full")
                author_in = ui.input("Author", value=state["author"]).props("dense").classes("w-full")
                series_in = ui.input("Series", value=state["series"]).props("dense").classes("w-full")
                asin_in = ui.input("ASIN", value=state["asin"]).props("dense").classes("w-full")
                isbn_in = ui.input("ISBN", value=state["isbn"]).props("dense").classes("w-full")
                ui.label("Search with").classes("text-caption colophon-muted q-mt-sm")
                service_radio = ui.radio(dict(services), value=state["service"]).props("dense")

                async def _go() -> None:
                    state.update(
                        title=title_in.value, author=author_in.value,
                        series=series_in.value, asin=asin_in.value,
                        isbn=isbn_in.value, service=service_radio.value,
                    )
                    await run_search()

                dialog_actions(dialog, confirm_label="Search", confirm_icon="search", on_confirm=_go, confirm_props="")

        def show_searching() -> None:
            body.clear()
            with body, ui.row().classes("items-center q-gutter-sm q-pa-md"):
                ui.spinner()
                ui.label(f"Searching {service_label.get(state['service'], '')}…")

        async def run_search() -> None:
            show_searching()
            try:
                results = await controller.search_matches(
                    book, title=state["title"], author=state["author"],
                    series=state["series"], asin=state["asin"],
                    isbn=state["isbn"], source_name=state["service"],
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
                        "text-caption colophon-muted"
                    )
                if not matches:
                    ui.label("No matches found").classes("colophon-muted q-pa-sm")
                with ui.list().props("dense").classes("w-full"):
                    for m in matches[:10]:
                        with ui.item(on_click=lambda result=m: show_picker(result)).props("clickable"):
                            with ui.item_section().props("avatar"):
                                _cover_thumb(m.cover_url)
                            with ui.item_section():
                                ui.item_label(m.title or "?")
                                _candidate_meta(
                                    m, book, source_label=controller.source_label(m.provider)
                                )

        def show_picker(result) -> None:
            body.clear()
            checks: dict[str, ui.checkbox] = {}
            with body:
                ui.button("Back to matches", icon="arrow_back", on_click=show_candidates).props(
                    "flat dense no-caps"
                )
                # Edition-specific fields from a non-audiobook source describe the wrong product,
                # so they're offered but unchecked by default (opt-in) per config.strict_source_fields.
                unchecked = controller.unchecked_match_fields(result)
                with ui.scroll_area().classes("w-full").style("max-height: 45vh"):
                    with ui.list().props("dense").classes("w-full"):
                        for key, source in controller.match_field_values(result).items():
                            current = get_field(book, key)
                            with ui.item():
                                with ui.item_section().props("avatar"):
                                    checks[key] = ui.checkbox(
                                        value=(source != (current or None) and key not in unchecked)
                                    )
                                with ui.item_section():
                                    ui.item_label(f"{field_labels.get(key, key)}: {source}")
                                    ui.item_label(f"current: {current or '(none)'}").props("caption")
                        if result.cover_url:
                            with ui.item():
                                with ui.item_section().props("avatar"):
                                    checks["cover"] = ui.checkbox(value=(result.cover_url != book.cover_url))
                                with ui.item_section():
                                    ui.item_label("Cover art")
                                    ui.item_label(result.cover_url).props("caption")

                def _apply(res=result) -> None:
                    selected = {k for k, c in checks.items() if c.value}
                    if not selected:
                        ui.notify("No fields selected")
                        return
                    controller.apply_match_fields(book, res, selected)
                    dialog.close()
                    ui.notify(f"Applied {len(selected)} field(s) from {controller.source_label(res.provider)}")
                    refresh_list()
                    show_detail(book.id)

                dialog_actions(dialog, confirm_label="Apply selected", confirm_icon="done_all", on_confirm=_apply, confirm_props="")

        show_form()
    dialog.open()


async def tag_dialog(
    controller: AppController,
    book: BookUnit,
    *,
    refresh_list: Callable[[], None],
    refresh_status: Callable[[], None],
    save_pending: Callable[[], bool],
) -> None:
    """Preview and write metadata tags to the book's files."""
    save_pending()  # "Write" encompasses Save: persist editor edits first
    plan = controller.tag_plan(book)
    with modal() as dialog, ui.card().classes("w-96"):
        ui.label(f"Write tags to {len(plan.files)} file(s)").classes("text-subtitle1")
        for warning in plan.warnings:
            with ui.row().classes("items-center no-wrap"):
                ui.icon("warning", color="warning")
                ui.label(warning).classes("text-caption text-warning")
        if plan.embed_cover:
            ui.label("Cover art will be embedded.").classes("text-caption colophon-muted")
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
            status = ui.label().classes("text-caption colophon-muted q-mr-auto self-center")
            ui.button("Cancel", on_click=dialog.close).props("flat")
            commit_btn = ui.button("Write tags", icon="sell")

        async def _commit() -> None:
            status.set_text("Writing tags…")
            with busy(commit_btn):
                result = await controller.write_tags(book)
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

        commit_btn.on_click(single_flight(_commit))
    dialog.open()


async def bulk_tag_dialog(
    controller: AppController,
    books: list[BookUnit],
    *,
    clear_selection: Callable[[], None],
    apply_pending_bulk: Callable[[], int],
) -> None:
    """Preview and write metadata tags across multiple selected books."""
    apply_pending_bulk()  # "Write" encompasses Save: apply pending edits first
    plans = [(b, controller.tag_plan(b)) for b in books]
    total_files = sum(len(p.files) for _, p in plans)
    with modal() as dialog, ui.card().classes("w-96"):
        ui.label(f"Write tags to {len(books)} books ({total_files} files)").classes(
            "text-subtitle1"
        )
        statuses: dict[str, ui.item_label] = {}
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
                        with ui.item_section().props("side"):
                            statuses[b.id] = ui.item_label(
                                "blocking error — will skip" if has_blocking_error(b) else "queued"
                            ).props("caption")
        actions = ui.row().classes("w-full justify-end q-gutter-sm q-mt-sm")
        with actions:
            progress_label = ui.label().classes(
                "text-caption colophon-muted q-mr-auto self-center"
            )
            ui.button("Cancel", on_click=dialog.close).props("flat")
            commit_btn = ui.button("Write tags", icon="sell")

        async def _commit() -> None:
            progress_label.set_text(f"Writing tags… 0 / {len(books)} books")

            def _on_progress(done: int, book, result) -> None:
                if has_blocking_error(book):
                    statuses[book.id].set_text("skipped — blocking error")
                else:
                    statuses[book.id].set_text(
                        f"written ({result.written})" if not result.failed
                        else f"failed: {result.failed} file(s)"
                    )
                progress_label.set_text(f"Writing tags… {done} / {len(books)} books")

            with busy(commit_btn):
                results = await controller.write_tags_books(books, progress=_on_progress)
            wrote = sum(r.written for r in results)
            failed = sum(r.failed for r in results)
            skipped = sum(1 for b in books if has_blocking_error(b))
            actions.clear()
            with actions:
                note = f"Wrote {wrote} file(s) across {len(books)} books" + (
                    f", {failed} failed" if failed else ""
                ) + (f", {skipped} skipped (blocking errors)" if skipped else "")
                ui.label(note).classes("text-caption q-mr-auto self-center")
                ui.button(
                    "Undo",
                    icon="undo",
                    on_click=lambda: (
                        controller.undo_tag_batch(),
                        ui.notify("Reverted tag write (embedded covers kept)"),
                        dialog.close(),
                        clear_selection(),
                    ),
                ).props("flat")
                ui.button(
                    "Close", on_click=lambda: (dialog.close(), clear_selection())
                ).props("flat")
            # Selection is cleared when the results dialog is dismissed (above),
            # not here, so the per-book progress + summary stay visible meanwhile.

        commit_btn.on_click(single_flight(_commit))
    dialog.open()


async def quick_match_dialog(
    controller: AppController,
    books: list[BookUnit],
    *,
    clear_selection: Callable[[], None],
) -> None:
    """Bulk-identify selected books against sources and apply chosen matches."""
    sources = controller.available_sources()  # [(name, label), ...]
    with modal() as dialog, ui.card().classes("w-[32rem]"):
        title = ui.label(f"Quick Match {len(books)} books").classes("text-subtitle1")
        body = ui.column().classes("w-full")
        proposals: list = []

        def show_config() -> None:
            body.clear()
            title.set_text(f"Quick Match {len(books)} books")
            checks: dict[str, ui.checkbox] = {}
            field_checks: dict[str, ui.checkbox] = {}
            with body:
                ui.label("Search these sources").classes("text-caption colophon-muted")
                for name, label in sources:
                    checks[name] = ui.checkbox(label, value=True).props("dense")
                ui.label("Match using these fields").classes(
                    "text-caption colophon-muted q-mt-sm"
                )
                for key, flabel in (
                    ("title", "Title"),
                    ("author", "Author"),
                    ("series", "Series"),
                    ("asin", "ASIN"),
                    ("isbn", "ISBN"),
                ):
                    field_checks[key] = ui.checkbox(flabel, value=True).props("dense")

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

                dialog_actions(dialog, confirm_label="Search", confirm_icon="search", on_confirm=_search, confirm_props="")

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
                    with ui.column().classes("w-full gap-0"):
                        for p in proposals:
                            cur = p.book.title or "(untitled)"
                            if p.best is None:
                                with ui.row().classes("w-full items-center no-wrap q-py-xs"):
                                    ui.icon("block").classes("text-grey-5 q-mr-sm")
                                    with ui.column().classes("gap-0"):
                                        ui.label(cur)
                                        ui.label("no match").classes("text-caption colophon-muted")
                                continue
                            with ui.row().classes("w-full items-center no-wrap"):
                                checks[p.book.id] = ui.checkbox(value=p.confidence >= threshold)
                                exp = ui.expansion().classes("w-full")
                                with exp.add_slot("header"):
                                    with ui.row().classes("w-full items-center no-wrap q-gutter-sm"):
                                        _cover_thumb(p.best.cover_url)
                                        with ui.column().classes("gap-0"):
                                            ui.label(f"{cur} → {p.best.title or '?'}")
                                            ui.label(
                                                controller.source_label(p.best.provider)
                                            ).classes("text-caption colophon-muted")
                                        ui.space()
                                        ui.badge(f"{p.confidence:.0f}").props(
                                            f"color={_confidence_color(p.confidence)}"
                                        ).tooltip(
                                            "Match confidence: how closely this candidate "
                                            "matches the book. Higher means a closer match."
                                        )
                                with exp:
                                    _candidate_meta(
                                        p.best, p.book,
                                        source_label=controller.source_label(p.best.provider),
                                    )

                def _apply() -> None:
                    keep_ids = {bid for bid, c in checks.items() if c.value}
                    chosen = [p for p in proposals if p.book.id in keep_ids]
                    if not chosen:
                        ui.notify("Nothing selected")
                        return
                    summary = controller.quick_match_apply(chosen)
                    show_summary(summary)

                dialog_actions(dialog, confirm_label="Apply selected", confirm_icon="done_all", on_confirm=_apply, confirm_props="")

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
            clear_selection()

        show_config()
    dialog.open()


async def scan_dialog(
    controller: AppController,
    *,
    refresh_all: Callable[[], None],
    folder: Path | None = None,
    selected_ids: set[str] | None = None,
) -> None:
    """Scan the filesystem with per-run pattern overrides, scope, and phase controls,
    review the resulting changes, then apply them (merge new books/files, fill empties)."""
    cfg = controller.ctx.config
    with modal() as dialog, ui.card().classes("w-[28rem]"):
        body = ui.column().classes("w-full")

        def show_options() -> None:
            body.clear()
            with body:
                ui.label("Scan library").classes("text-subtitle1")

                selection = set(selected_ids or ())
                scan_paths = list(cfg.scan_paths)
                path_boxes: dict[Path, object] = {}
                if selection:
                    ui.label(f"Scanning: {len(selection)} selected books").classes(
                        "text-caption colophon-muted"
                    )
                elif folder is not None:
                    ui.label(f"Scanning: folder {folder.name}").classes("text-caption colophon-muted")
                elif len(scan_paths) > 1:
                    # Pick which configured scan paths to walk (default all) so a rebuild can
                    # target one path without re-touching confirmed ones.
                    ui.label("Scan paths").classes("colophon-seccap")
                    for p in scan_paths:
                        cb = ui.checkbox(p.name or str(p), value=True).props("dense")
                        cb.tooltip(str(p))
                        path_boxes[p] = cb
                else:
                    scope_text = f"path: {scan_paths[0].name}" if scan_paths else "all library paths"
                    ui.label(f"Scanning: {scope_text}").classes("text-caption colophon-muted")

                ui.label("Depth").classes("colophon-seccap")
                depth_choice = ui.radio(
                    {"new_changed": "New & changed", "rebuild": "Rebuild all"},
                    value="new_changed",
                ).props("dense")
                ui.label(
                    "New & changed: add new books and re-process changed folders. "
                    "Rebuild all: force a full re-derive of everything in scope."
                ).classes("text-caption colophon-muted")

                with ui.row().classes("w-full justify-end q-gutter-sm q-mt-sm"):
                    ui.button("Cancel", on_click=dialog.close).props("flat")

                    async def _run_scan() -> None:
                        if path_boxes:
                            roots = [p for p, cb in path_boxes.items() if cb.value]
                            if not roots:
                                ui.notify("Select at least one scan path", type="warning")
                                return
                        elif folder is not None and not selection:
                            roots = [folder]
                        else:
                            roots = None  # a selection uses book_ids; single/no path -> all
                        opts = ScanOptions(
                            scope=_DEPTH_TO_SCOPE[depth_choice.value],
                            phases=frozenset(LOCAL),
                            book_ids=selection or None,
                        )
                        body.clear()
                        with body:
                            ui.label("Scanning library").classes("text-subtitle1")
                            with ui.row().classes("items-center q-gutter-sm"):
                                ui.spinner()
                                prog = ui.label("Scanning…").classes(
                                    "text-caption colophon-muted"
                                )

                        def _progress(done: int, total: int, label: str) -> None:
                            prog.set_text(f"Scanning {done} / {total} · {label}")

                        try:
                            plan = await controller.scan_preview_streamed(
                                roots,
                                template=cfg.filename_template,
                                directory_scheme=cfg.directory_scheme,
                                options=opts,
                                progress=_progress,
                            )
                        except ValueError as e:
                            ui.notify(f"Invalid pattern: {e}", type="negative")
                            show_options()
                            return
                        changes = await asyncio.to_thread(controller.scan_plan_changes, plan)
                        show_results(plan, changes)

                    ui.button("Scan", icon="radar", on_click=_run_scan).props("unelevated")

        def show_results(plan, changes) -> None:
            body.clear()
            with body:
                if plan.new_books == 0 and plan.existing_books == 0:
                    ui.label("No changes found").classes("text-subtitle1")
                    with ui.row().classes("w-full justify-end q-mt-sm"):
                        ui.button("Back", on_click=show_options).props("flat")
                        ui.button("Close", on_click=dialog.close).props("flat")
                    return
                ui.label("Scan results").classes("text-subtitle1")
                ui.label(
                    f"{plan.new_books} new · {plan.existing_books} updated · "
                    f"{plan.files_added} files added"
                ).classes("text-caption colophon-muted")
                if changes:
                    ui.label("Changes to existing books").classes("text-caption q-mt-sm")
                    with ui.scroll_area().classes("w-full").style("max-height: 40vh"):
                        for ch in changes[:SCAN_DIFF_ROW_CAP]:
                            ui.label(
                                f"{ch.title} · {ch.field}: "
                                f"{ch.before or '(none)'} → {ch.after or '(none)'}"
                            ).classes("text-caption colophon-muted")
                    if len(changes) > SCAN_DIFF_ROW_CAP:
                        ui.label(f"…and {len(changes) - SCAN_DIFF_ROW_CAP} more") \
                            .classes("text-caption colophon-muted")

                async def _apply() -> None:
                    dialog.close()
                    written = await asyncio.to_thread(controller.apply_scan, plan)
                    # The dialog's slot is gone after close(); guard the post-await UI updates so a
                    # closed/navigated/disconnected client can't turn a committed scan into a crash.
                    _safe_ui(refresh_all)
                    _safe_ui(lambda: ui.notify(f"Applied {written} books to the library"))

                with ui.row().classes("w-full justify-end q-gutter-sm q-mt-sm"):
                    ui.button("Back", on_click=show_options).props("flat")
                    ui.button("Apply", icon="check", on_click=_apply).props("unelevated")

        dialog.open()
        show_options()


async def match_dialog(
    controller: AppController, *, refresh_all: Callable[[], None], selected_ids: set[str],
    on_review_weak: Callable[[], None],
) -> None:
    """Match books against the metadata sources. First choose the scope (Selected / Ready / All)
    — warning when the set carries weakly-identified books whose match query is only a guess —
    then preview per-book matches and apply (fill empties, route review) or retry the misses."""
    with modal() as dialog, ui.card().classes("w-[28rem]"):
        body = ui.column().classes("w-full")

        def show_scope() -> None:
            body.clear()
            with body:
                ui.label("Match against sources").classes("text-subtitle1")
                ui.label("Look up metadata and preview matches before applying.").classes(
                    "text-caption colophon-muted"
                )
                scope = scope_selector(
                    controller, selected_ids,
                    ready_label="Identified", ready_state=BookState.IDENTIFIED,
                )
                scope_hint = ui.label(
                    "Identified: books with an inferred identity, ready to match against sources."
                ).classes("text-caption colophon-muted")
                with ui.row().classes("items-center q-gutter-xs q-mt-xs") as warn_row:
                    warn = ui.label("").classes("text-caption text-warning")
                    ui.button(
                        "Review in Library", icon="filter_alt",
                        on_click=lambda: (dialog.close(), on_review_weak()),
                    ).props("flat dense no-caps color=warning")

                def _refresh_warn() -> None:
                    books = controller.books_for_scope(
                        scope.value, selected_ids, ready_state=BookState.IDENTIFIED
                    )
                    weak = sum(1 for b in books if has_weak_identity(b))
                    warn.set_text(
                        f"⚠ {weak} of {len(books)} have only a weakly-inferred identity — "
                        f"matches may be unreliable." if weak else ""
                    )
                    warn_row.set_visibility(bool(weak))

                def _sync_scope_hint() -> None:
                    # The hint explains the Identified scope; hide it for Selected / All.
                    scope_hint.set_visibility(scope.value == "ready")

                scope.on_value_change(lambda _e: (_refresh_warn(), _sync_scope_hint()))
                _refresh_warn()
                _sync_scope_hint()

                with ui.row().classes("w-full justify-end q-gutter-sm q-mt-sm"):
                    ui.button("Cancel", on_click=dialog.close).props("flat")
                    ui.button(
                        "Match", icon="join_inner", on_click=lambda: _start(scope.value)
                    ).props("unelevated")

        async def _start(scope_value: str) -> None:
            books = controller.books_for_scope(
                scope_value, selected_ids, ready_state=BookState.IDENTIFIED
            )
            if not books:
                ui.notify("No books in that scope")
                return
            await run_match(books)

        async def run_match(books: list[BookUnit]) -> None:
            body.clear()
            with body:
                ui.label(f"Matching {len(books)} book(s)").classes("text-subtitle1")
                log = BatchLog([BatchItem(b.id, b.title or "(untitled)") for b in books])
            state = {"cancelled": False}

            def _cancel() -> None:
                state["cancelled"] = True
                dialog.close()

            def _progress(book_id: str, kind: str) -> None:
                log.update(book_id, "match found" if kind == "ok" else "no match", kind=kind)

            def _summary(plan) -> str:
                no_match = sum(1 for p in plan.proposals if p.best is None)
                parts = [f"{plan.to_apply} auto-matched", f"{plan.to_review} review"]
                if no_match:
                    parts.append(f"{no_match} no match")
                if plan.skipped:
                    parts.append(f"{plan.skipped} skipped")
                return "  ·  ".join(parts)

            async def _apply(plan) -> None:
                dialog.close()
                summary = await asyncio.to_thread(controller.apply_identify, plan)
                actions = (
                    [{
                        "label": "Undo", "color": "white",
                        "handler": lambda b=summary.batch_id: (controller.undo(b), refresh_all()),
                    }]
                    if summary.batch_id else None
                )
                # The dialog's slot is gone after close(); guard the post-await UI updates.
                _safe_ui(refresh_all)
                _safe_ui(lambda: ui.notify(
                    f"Matched {summary.auto_matched} book(s); "
                    f"{summary.routed_to_review} need review",
                    actions=actions,
                ))

            async def _retry(plan, ids: list[str]) -> None:
                new_plan = await controller.retry_identify(plan, ids, progress=_progress)
                _finish(new_plan)

            def _finish(plan) -> None:
                log.finish(
                    _summary(plan),
                    on_close=dialog.close,
                    on_retry=lambda ids, p=plan: _retry(p, ids),
                    extra=[("Apply", "done_all", lambda p=plan: _apply(p))],
                )

            log.cancel_action(_cancel)
            plan = await controller.identify_preview(books, progress=_progress)
            if state["cancelled"]:
                return
            _finish(plan)

        dialog.open()
        show_scope()


def remove_from_library_dialog(
    controller: AppController,
    book_ids: list[str],
    *,
    label: str,
    on_done: Callable[[], None],
) -> None:
    """Confirm, then forget the given book(s): drop the record, edit history,
    operations, and graph nodes, but leave every file on disk. `label` names the
    target in the prompt ('"Dune"' for a single book, '3 books' for a bulk
    selection). `on_done` runs after a successful removal so the caller can
    repaint and clear any selection. There is no undo; re-scanning is the recovery
    path, and the copy says so."""
    with modal() as dialog, ui.card().classes("w-[28rem]"):
        ui.label(f"Remove {label} from the library?").classes("text-subtitle1")
        ui.label(
            "The audio files stay on disk. Colophon's record, your edits, and the "
            "confirmed identity are discarded. Re-scan to restore."
        ).classes("text-caption colophon-muted")

        async def _confirm() -> None:
            with busy(confirm_btn):
                removed = await asyncio.to_thread(controller.cleanup_remove, book_ids)
            dialog.close()
            ui.notify(
                "Removed from library"
                if removed == 1
                else f"Removed {removed} books from library"
            )
            on_done()

        confirm_btn = dialog_actions(
            dialog,
            confirm_label="Remove",
            confirm_icon="delete_outline",
            on_confirm=_confirm,
            confirm_props="unelevated color=negative",
        )
    dialog.open()


def delete_summary(paths: list[Path], *, book_removed: bool) -> str:
    """Plain-language description of an irreversible delete, for the confirm dialog."""
    if book_removed and not paths:
        return "This removes the book and its Colophon record. Its files are already missing from disk."
    names = ", ".join(p.name for p in paths)
    tail = " The book has no other audio, so it will be removed too." if book_removed else ""
    return f"This permanently deletes {len(paths)} file(s) from disk: {names}.{tail}"


def confirm_delete_dialog(paths: list[Path], *, book_removed: bool, on_confirm: Callable[[], None]) -> None:
    """A persistent confirm for an irreversible delete. Names exactly what will go; the confirm
    button runs `on_confirm` (which does the deletion) then closes."""
    dialog = modal()
    with dialog, ui.card().classes("q-pa-md").style("min-width: 22rem"):
        ui.label("Delete permanently?").classes("text-subtitle1")
        ui.label(delete_summary(paths, book_removed=book_removed)).classes("colophon-muted text-caption")

        def _go(btn) -> None:
            with busy(btn):
                on_confirm()
            dialog.close()

        btn = dialog_actions(dialog, confirm_label="Delete", confirm_icon="delete",
                             on_confirm=lambda: None, confirm_props="unelevated color=negative")
        btn.on("click", lambda: _go(btn))
    dialog.open()


async def persist_dialog(
    controller: AppController,
    *,
    refresh_all: Callable[[], None],
    selected_ids: set[str],
    clear_selection: Callable[[], None],
) -> None:
    """Write curated metadata out. Choose a scope (Selected / Ready / All) and one or more of
    Tag / Organize / Encode; warn when the scope includes books that aren't Ready/confirmed, and
    when Encode is chosen (it can be slow per book). Runs the chosen operations in the background."""
    from colophon.controller import CancelToken, EncodeJobOptions

    with modal() as dialog, ui.card().classes("w-[30rem]"):
        cfg = controller.ctx.config
        _PREVIEW_CAP = 50
        body = ui.column().classes("w-full")

        def _close() -> None:
            dialog.close()
            refresh_all()

        def _patterns(folder_pat, file_pat) -> PathPatterns:
            return PathPatterns(
                folder=folder_pat.value or cfg.organize_folder_pattern,
                single_file=file_pat.value or cfg.organize_file_pattern,
                series_pattern=cfg.series_pattern,
                series_name_pattern=cfg.series_name_pattern,
                series_number_pattern=cfg.series_number_pattern,
            )

        def show_options() -> None:
            body.clear()
            with body:
                ui.label("Persist changes").classes("text-subtitle1")
                ui.label(
                    "Write your curated metadata out to the files and library."
                ).classes("text-caption colophon-muted")
                scope = scope_selector(controller, selected_ids)

                ui.label("Operations").classes("colophon-seccap q-mt-sm")
                tag = ui.checkbox("Tag — write metadata into the files").props("dense")
                org = ui.checkbox("Organize — move into the library").props("dense")
                enc = ui.checkbox("Encode — re-encode to M4B").props("dense")
                ui.label(
                    "Encoding re-encodes each book and can take a long time per book — minutes to "
                    "hours depending on length."
                ).classes("text-caption text-warning").bind_visibility_from(enc, "value")

                folder_pat = ui.input("Folder pattern", value=cfg.organize_folder_pattern).props(
                    "outlined dense"
                ).classes("w-full")
                file_pat = ui.input("File name pattern", value=cfg.organize_file_pattern).props(
                    "outlined dense"
                ).classes("w-full")
                pat_hint = ui.label(
                    "Wrap optional text in [ ... ] so it appears only when its token has a "
                    "value, e.g. [$SerNum - ]$Title. Use [[ and ]] for literal brackets."
                ).classes("text-caption colophon-muted")
                preview = ui.label("").classes("text-caption colophon-muted")
                for el in (folder_pat, file_pat, pat_hint, preview):
                    el.bind_visibility_from(org, "value")

                def _preview() -> None:
                    preview.set_text("Structure: " + sample_target(folder_pat.value, file_pat.value))

                folder_pat.on_value_change(lambda _e: _preview())
                file_pat.on_value_change(lambda _e: _preview())
                _preview()
                attach_history_menu(
                    folder_pat, cfg.recent_organize_patterns,
                    lambda op: f"{op.folder} · {op.file}",
                    lambda op: (folder_pat.set_value(op.folder), file_pat.set_value(op.file),
                                _preview()),
                    tooltip="Recent folder + file patterns",
                )
                conc = ui.number("Concurrency", value=2, min=1, max=8, format="%d").props(
                    "dense"
                ).classes("w-32")
                conc.bind_visibility_from(enc, "value")
                dele = ui.checkbox("Delete source files after (verified)").props("dense")
                dele.bind_visibility_from(enc, "value")
                rem = ui.checkbox("Remove from library after organizing").props("dense")
                rem.bind_visibility_from(org, "value")
                ui.label(
                    "Removed books leave Colophon; their organized files stay at the "
                    "destination. Colophon won't manage them again unless the destination is "
                    "added to the scan paths."
                ).classes("text-caption colophon-muted").bind_visibility_from(rem, "value")

                block_warn = ui.label("").classes("text-caption text-negative q-mt-xs")
                warn = ui.label("").classes("text-caption text-warning q-mt-xs")

                def _refresh_warn() -> None:
                    books = controller.books_for_scope(scope.value, selected_ids)
                    persistable = [b for b in books if not has_blocking_error(b)]
                    blocked = len(books) - len(persistable)
                    notready = sum(1 for b in persistable if not is_ready_to_persist(b))
                    block_warn.set_text(
                        f"⛔ {blocked} of {len(books)} have a blocking error (missing or corrupt "
                        f"files) and will be skipped." if blocked else ""
                    )
                    warn.set_text(
                        f"⚠ {notready} of {len(persistable)} aren't marked Ready/confirmed — "
                        f"persisting may write unverified metadata." if notready else ""
                    )

                scope.on_value_change(lambda _e: _refresh_warn())
                _refresh_warn()

                with ui.row().classes("w-full justify-end q-gutter-sm q-mt-sm"):
                    ui.button("Cancel", on_click=dialog.close).props("flat")

                    async def _run() -> None:
                        if not (tag.value or org.value or enc.value):
                            ui.notify("Choose at least one operation")
                            return
                        if dele.value and not enc.value:
                            ui.notify("Delete sources requires Encode")
                            return
                        books = controller.books_for_scope(scope.value, selected_ids)
                        if not books:
                            ui.notify("No books in that scope")
                            return
                        # Blocking errors (missing/corrupt files) can't be persisted — drop them
                        # here so we never attempt a write that would error.
                        blocked = [b for b in books if has_blocking_error(b)]
                        books = [b for b in books if not has_blocking_error(b)]
                        if not books:
                            ui.notify(
                                "Every selected book has a blocking error (missing or corrupt "
                                "files) — nothing to persist", type="negative",
                            )
                            return
                        if blocked:
                            ui.notify(
                                f"Skipping {len(blocked)} book(s) with a blocking error",
                                type="warning",
                            )
                        opts = EncodeJobOptions(
                            encode=bool(enc.value), organize=bool(org.value),
                            delete_sources=bool(dele.value), concurrency=int(conc.value or 1),
                            patterns=_patterns(folder_pat, file_pat),
                        )
                        if opts.organize:
                            controller.record_organize_pattern(
                                opts.patterns.folder, opts.patterns.single_file
                            )
                            show_preview(books, bool(tag.value), opts, bool(rem.value))
                        else:
                            notready = sum(1 for b in books if not is_ready_to_persist(b))
                            if notready:
                                _confirm(books, bool(tag.value), opts, notready)
                            else:
                                await run_persist(books, bool(tag.value), opts, False)

                    ui.button("Persist", icon="save", on_click=_run).props("unelevated")

        def _confirm(books, do_tag, opts, notready) -> None:
            body.clear()
            with body:
                with ui.row().classes("items-center q-gutter-sm"):
                    ui.icon("warning", color="warning", size="1.5rem")
                    ui.label("Some books aren't ready").classes("text-subtitle1")
                ui.label(
                    f"{notready} of {len(books)} books aren't marked Ready or confirmed. Persisting "
                    f"now may tag, move, or encode books with unverified metadata."
                ).classes("text-body2 colophon-muted")
                with ui.row().classes("w-full justify-end q-gutter-sm q-mt-sm"):
                    ui.button("Back", on_click=show_options).props("flat")
                    ui.button(
                        "Persist anyway", icon="save",
                        on_click=lambda: run_persist(books, do_tag, opts, False),
                    ).props("unelevated color=warning")

        def show_preview(books, do_tag, opts, remove_after) -> None:
            rows = controller.organize_preview(books, patterns=opts.patterns, encode=opts.encode)
            body.clear()
            with body:
                ui.label("Confirm destinations").classes("text-subtitle1")
                ui.label(
                    "Where each book will be organized under the library. Nothing moves "
                    "until you confirm."
                ).classes("text-caption colophon-muted")
                collisions = sum(1 for r in rows if r.collision)
                if collisions:
                    ui.label(
                        f"⚠ {collisions} destination(s) already exist — those books won't be "
                        f"moved (they'll fail rather than overwrite)."
                    ).classes("text-caption text-warning")
                notready = sum(1 for b in books if not is_ready_to_persist(b))
                if notready:
                    ui.label(
                        f"⚠ {notready} of {len(books)} aren't marked Ready/confirmed — "
                        f"persisting may write unverified metadata."
                    ).classes("text-caption text-warning")
                # The full destination path can be long; let the list scroll horizontally
                # (and vertically) rather than truncate it, so the whole path is readable.
                with ui.column().classes("q-gutter-none").style(
                    "max-height: 16rem; max-width: 100%; overflow: auto"
                ):
                    for r in rows[:_PREVIEW_CAP]:
                        with ui.row().classes("items-center no-wrap q-gutter-sm").style(
                            "white-space: nowrap"
                        ):
                            ui.icon(
                                "warning" if (r.collision or r.blocked) else "east", size="1rem"
                            ).classes("colophon-muted")
                            ui.label(r.title)
                            ui.label(str(r.target)).classes("text-caption colophon-muted")
                    if len(rows) > _PREVIEW_CAP:
                        ui.label(f"…and {len(rows) - _PREVIEW_CAP} more").classes(
                            "text-caption colophon-muted"
                        )
                if remove_after:
                    ui.label(
                        "These books will be removed from the library after organizing."
                    ).classes("text-caption text-warning")
                with ui.row().classes("w-full justify-end q-gutter-sm q-mt-sm"):
                    ui.button("Back", on_click=show_options).props("flat")
                    # When some books aren't Ready, keep the not-ready acknowledgment visible on
                    # the confirm action itself (warning colour + the count), so it can't be read
                    # past — the preview replaces the old dedicated not-ready confirm screen.
                    confirm_label = (
                        f"Confirm & persist ({notready} not ready)" if notready else "Confirm & persist"
                    )
                    confirm_props = "unelevated color=warning" if notready else "unelevated"
                    ui.button(
                        confirm_label, icon="save",
                        on_click=lambda: run_persist(books, do_tag, opts, remove_after),
                    ).props(confirm_props)

        async def run_persist(books, do_tag, opts, remove_after=False) -> None:
            body.clear()
            token = CancelToken()
            _KIND = {"done": "ok", "failed": "fail", "cancelled": "skip", "skipped": "skip"}
            _TERMINAL = {"done", "failed", "cancelled", "skipped"}
            with body:
                ui.label(f"Persisting {len(books)} book(s)").classes("text-subtitle1")
                log = BatchLog([BatchItem(b.id, b.title or "(untitled)") for b in books])
            log.cancel_action(token.cancel)

            def _retry(ids: list[str]) -> object:
                return run_persist([b for b in books if b.id in ids], do_tag, opts, remove_after)

            # Live progress across both phases. Tag and encode/organize each contribute one
            # unit of work per book, so the total spans whichever operations were chosen.
            do_process = opts.encode or opts.organize
            total = len(books) * (int(do_tag) + int(do_process))
            prog = {"done": 0, "fail": 0}

            def _tick(failed: bool) -> None:
                prog["done"] += 1
                prog["fail"] += int(failed)
                log.set_progress(prog["done"], total, failed=prog["fail"])

            try:
                if do_tag:
                    def _on_tag(done, book, res) -> None:
                        log.update(
                            book.id,
                            "tagged" if res.ok else f"tag failed: {res.failed} file(s)",
                            kind="ok" if res.ok else "fail",
                        )
                        _tick(not res.ok)

                    await controller.write_tags_books(books, progress=_on_tag)
                if do_process:
                    def _on_process(bid, status) -> None:
                        log.update(bid, status, kind=_KIND.get(status, "running"))
                        if status in _TERMINAL:
                            _tick(status == "failed")

                    job = await controller.run_encode_job(books, opts, progress=_on_process, cancel=token)
                    # The progress callback only carries a status; the returned results carry the
                    # reason. Surface it on failed/skipped rows so it stays readable after the run.
                    for r in job.results:
                        if r.status in {"failed", "skipped"} and r.detail:
                            log.update(r.book_id, f"{r.status}: {r.detail}",
                                       kind=_KIND.get(r.status, "fail"))
                    if remove_after and opts.organize:
                        removed_ids = [r.book_id for r in job.results if r.status == "done"]
                        if removed_ids:
                            n = await asyncio.to_thread(controller.remove_from_library, removed_ids)
                            for bid in removed_ids:
                                log.update(bid, "removed from library", kind="ok")
                            logger.info(f"removed {n} organized book(s) from the library")
                clear_selection()
                await controller.trigger_abs_scan()  # best-effort library rescan

                c = log.counts()
                note = f"{c.get('ok', 0)} done"
                if c.get("fail"):
                    note += f", {c['fail']} failed"
                if c.get("skip"):
                    note += f", {c['skip']} skipped"
                log.finish(note, on_close=_close, on_retry=_retry)
            except Exception as exc:
                # Never leave the persist stopped with no explanation: an error mid-run (a store
                # write, the ABS rescan, an unexpected raise) surfaces on the log with a reason and
                # a retry, instead of a silently frozen dialog.
                logger.exception("persist run failed")
                log.finish(f"Persist error — {type(exc).__name__}: {exc}",
                           on_close=_close, on_retry=_retry)

        dialog.open()
        show_options()
