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


@contextmanager
def page_toolbar() -> Iterator[None]:
    """A recessive sub-header band for a page's controls and state-of-play, set off from
    the body by a surface step and a hairline rule (the page -> surface -> line tonal
    rule). Put the page's control row(s) and any summary/worklist inside the `with` body;
    the page body that follows sits on the warm page background, so the two zones read as
    distinct. Shared so every page gains the same hierarchy in one place."""
    with ui.column().classes("colophon-toolbar w-full q-gutter-xs q-pa-none"):
        yield


def body_column(measure: str = "full") -> ui.column:
    """The standard page body column, returned for pages that clear and rebuild it
    dynamically (the graph worklist). Most pages use `page_body` instead. One gutter and
    block rhythm everywhere: `measure="full"` fills the width (dense lists, trees, card
    grids); `measure="read"` caps a left-anchored reading column so forms and prose never
    stretch to an unscannable width."""
    col = ui.column().classes("w-full q-pa-md gap-4")
    if measure == "read":
        col.classes("colophon-measure-read")
    return col


@contextmanager
def page_body(measure: str = "full") -> Iterator[None]:
    """The standard page content region below the header (and optional `page_toolbar`).
    See `body_column` for the `measure` options. Use this context-manager form on pages
    whose body is built once; use `body_column` where the body is rebuilt on the fly."""
    with body_column(measure):
        yield


@contextmanager
def page_section(title: str, subtitle: str | None = None) -> Iterator[None]:
    """A titled content card: a heading, an optional caption, and a body stack that the
    `with` body fills. Shared so every page groups content the same way (settings panels,
    stats blocks), instead of each page carrying its own near-identical section helper."""
    with ui.card().classes("w-full q-pa-md"):
        ui.label(title).classes("text-subtitle1 text-weight-medium")
        if subtitle:
            ui.label(subtitle).classes("text-caption colophon-muted q-mb-xs")
        with ui.column().classes("w-full gap-3 q-mt-sm"):
            yield
