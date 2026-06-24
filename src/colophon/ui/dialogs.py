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
