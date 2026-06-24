"""Shared page chrome: the theme preamble plus the standard app header.

`page_header` folds the per-page boilerplate (apply_theme + setup_dark_mode, then
the elevated header with the brand icon, label, nav tabs, spacer, and dark-mode
toggle) into one context manager. The body of the `with` adds any page-specific
action buttons, which land between the spacer and the dark-mode toggle.
"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager

from nicegui import ui

from colophon.controller import AppController
from colophon.ui.tabs import app_tabs
from colophon.ui.theme import apply_theme, dark_mode_button, setup_dark_mode


@contextmanager
def page_header(
    controller: AppController, active: str, *, icon: str, label: str = "Colophon"
) -> Iterator[None]:
    """Standard page chrome. `active` is the nav tab to highlight; `icon` is the
    brand icon for the page; `label` defaults to the app name. Action buttons added
    in the `with` body appear between the spacer and the dark-mode toggle."""
    apply_theme()
    dark = setup_dark_mode()
    with ui.header(elevated=True).classes("items-center q-px-md"):
        ui.icon(icon, color="primary").classes("text-h5")
        ui.label(label).classes("text-h6 q-ml-sm text-weight-medium")
        app_tabs(controller, active)
        ui.space()
        yield
        dark_mode_button(dark)
