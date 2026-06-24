"""Dialog builders for the Library workspace, factored out of workspace.py.

Each builder is a standalone function taking the controller, the target book(s),
and explicit callbacks (refresh/show/clear) instead of closing over
render_workspace locals. `dialog_actions` and `busy` collapse the repeated
Cancel/confirm action row and the loading-button pattern.
"""

from __future__ import annotations

from collections.abc import Callable, Iterator
from contextlib import contextmanager
from pathlib import Path

from nicegui import ui

from colophon.controller import AppController
from colophon.core.fields import EDITABLE_FIELDS
from colophon.core.models import BookUnit


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
