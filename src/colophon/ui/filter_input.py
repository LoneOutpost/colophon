"""The app's single filter widget.

One styled, debounced search field with a leading icon, a clear button, and
Esc-to-clear, so every filter in the app (the library filter, each Manage tab)
looks and behaves identically. Callers bind it to their own data; the widget only
reports the trimmed text.
"""

from __future__ import annotations

from collections.abc import Callable

from nicegui import ui


def filter_input(
    on_text: Callable[[str], None],
    *,
    placeholder: str = "Filter…",
    value: str = "",
    aria_label: str = "Filter",
    debounce: int = 300,
) -> ui.input:
    """A dense, clearable search field with a leading icon and Esc-to-clear. Fires `on_text` with
    the trimmed value (empty string on clear or Esc). Returns the input so a caller can register a
    focus shortcut or add width classes. Does not set a width class — the caller sizes it."""
    inp = ui.input(placeholder=placeholder, value=value).props(
        f'dense outlined clearable debounce={debounce} aria-label="{aria_label}"'
    )
    with inp.add_slot("prepend"):
        ui.icon("search").classes("colophon-muted")
    inp.on_value_change(lambda e: on_text((e.value or "").strip()))

    def _clear() -> None:
        inp.set_value("")           # fires on_value_change -> on_text("")
        inp.run_method("blur")      # hand keyboard control back to the content

    inp.on("keydown.esc", _clear)
    return inp
