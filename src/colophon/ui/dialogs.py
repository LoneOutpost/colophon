"""Dialog builders for the Library workspace, factored out of workspace.py.

Each builder is a standalone function taking the controller, the target book(s),
and explicit callbacks (refresh/show/clear) instead of closing over
render_workspace locals. `dialog_actions` and `busy` collapse the repeated
Cancel/confirm action row and the loading-button pattern.
"""

from __future__ import annotations

import logging
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from pathlib import Path

from nicegui import ui

from colophon.controller import AppController
from colophon.core.fields import EDITABLE_FIELDS, get_field
from colophon.core.models import BookUnit
from colophon.core.sources import SourceResult

logger = logging.getLogger(__name__)


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
    """Show a button's spinner for the duration of an action, always clearing it
    (replaces the hand-rolled props('loading=true') ... finally remove pattern)."""
    button.props("loading=true")
    try:
        yield
    finally:
        button.props(remove="loading")


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


def _candidate_meta(result: SourceResult, book: BookUnit, *, source_label: str) -> None:
    """Render a candidate's metadata block (captions + runtime/abridged row),
    comparing runtime against `book`. Emits NiceGUI elements into the current
    layout context; the caller owns any surrounding row/checkbox/expansion.
    Empty fields are omitted."""
    authors = ", ".join(result.authors) or "unknown"
    year = f" ({result.publish_year})" if result.publish_year else ""
    ui.item_label(f"{source_label} · {authors}{year}").props("caption")

    if result.narrators:
        ui.item_label(f"Narr: {', '.join(result.narrators)}").props("caption")

    series = _fmt_series_label(result.series_name, result.series_sequence)
    pub_bits = [bit for bit in (series, result.publisher) if bit]
    if pub_bits:
        ui.item_label(" · ".join(pub_bits)).props("caption")

    rt = _fmt_runtime_delta(result.runtime_ms, book.duration_ms)
    if rt or result.abridged is not None:
        with ui.row().classes("items-center no-wrap q-gutter-xs"):
            if rt:
                ui.item_label(rt).props("caption").classes("colophon-mono")
            if result.abridged is not None:
                ui.badge("Abridged" if result.abridged else "Unabridged").props("color=grey-6 outline")


def remap_dialog(
    controller: AppController,
    book: BookUnit,
    *,
    refresh_list: Callable[[], None],
    show_detail: Callable[[str], None],
) -> None:
    """Move one field's value into another field (fixes mis-tagging)."""
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
            controller.remap(book, src=src.value, dst=dst.value, clear_source=clear.value)
            dialog.close()
            ui.notify(f"Remapped {src.value} to {dst.value}")
            refresh_list()
            show_detail(book.id)

        dialog_actions(dialog, confirm_label="Remap", confirm_icon="swap_horiz", on_confirm=_apply)
    dialog.open()


def rename_dialog(
    controller: AppController,
    book: BookUnit,
    sf_path: Path,
    *,
    show_detail: Callable[[str], None],
) -> None:
    """Rename a single source file of the book."""
    with ui.dialog() as dialog, ui.card():
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
    with ui.dialog() as dialog, ui.card().classes("w-[28rem]"):
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
                    ui.label("No covers found").classes("text-grey-6")
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

    with ui.dialog() as dialog, ui.card().classes("w-96"):
        ui.label(f"Find matches for {book.title or '(untitled)'}").classes("text-subtitle1")
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
                isbn_in = ui.input("ISBN", value=state["isbn"]).props("dense").classes("w-full")
                ui.label("Search with").classes("text-caption text-grey-7 q-mt-sm")
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
                        "text-caption text-grey-6"
                    )
                if not matches:
                    ui.label("No matches found").classes("text-grey-6 q-pa-sm")
                with ui.list().props("dense").classes("w-full"):
                    for m in matches[:10]:
                        with ui.item(on_click=lambda result=m: show_picker(result)).props("clickable"):
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
                with ui.scroll_area().classes("w-full").style("max-height: 45vh"):
                    with ui.list().props("dense").classes("w-full"):
                        for key, source in controller.match_field_values(result).items():
                            current = get_field(book, key)
                            with ui.item():
                                with ui.item_section().props("avatar"):
                                    checks[key] = ui.checkbox(value=(source != (current or None)))
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

        commit_btn.on_click(_commit)
    dialog.open()
