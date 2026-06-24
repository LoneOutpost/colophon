"""Dialog builders for the Library workspace, factored out of workspace.py.

Each builder is a standalone function taking the controller, the target book(s),
and explicit callbacks (refresh/show/clear) instead of closing over
render_workspace locals. `dialog_actions` and `busy` collapse the repeated
Cancel/confirm action row and the loading-button pattern.
"""

from __future__ import annotations

from collections.abc import Callable, Iterator
from contextlib import contextmanager

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
