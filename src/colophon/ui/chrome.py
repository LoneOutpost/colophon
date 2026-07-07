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
        jobs_indicator(controller)
        dark_mode_button(dark)


def jobs_indicator(controller: AppController) -> None:
    """A cross-session indicator of running background jobs (scan, re-probe, encode, downloads).
    Reads the shared server-side registry, so every open browser shows the same live set. A compact
    spinner + count chip in the app bar, click for a popover with each job's progress; hidden when
    idle. A short poll keeps it live without a websocket push. The button and menu are persistent —
    only the inner list re-renders — so an open popover stays open while its progress updates."""
    btn = ui.button().props("flat dense round").classes("colophon-jobs-chip")
    with btn:
        ui.spinner(size="sm", color="primary")
        badge = ui.badge("").props("floating color=primary rounded")
        with ui.menu().props("anchor='bottom right' self='top right'").classes("colophon-jobs-menu"):
            ui.item_label("Active jobs").props("header").classes("text-weight-medium")

            @ui.refreshable
            def job_list() -> None:
                for j in controller.active_jobs():
                    with ui.item(), ui.item_section():
                        ui.item_label(j.label)
                        frac = j.fraction
                        if frac is not None:
                            ui.linear_progress(value=frac, size="6px").props(
                                "instant-feedback rounded color=primary"
                            ).classes("q-mt-xs")
                            tail = f" · {j.detail}" if j.detail else ""
                            ui.item_label(f"{j.done} / {j.total}{tail}").props("caption")
                        elif j.detail:
                            ui.item_label(j.detail).props("caption")

            job_list()

    def tick() -> None:
        jobs = controller.active_jobs()
        btn.set_visibility(bool(jobs))
        badge.set_text(str(len(jobs)))
        job_list.refresh()

    ui.timer(1.5, tick)
    tick()


@contextmanager
def page_toolbar(*, sticky: bool = False) -> Iterator[None]:
    """A recessive sub-header band for a page's controls and state-of-play, set off from
    the body by a surface step and a hairline rule (the page -> surface -> line tonal
    rule). Put the page's control row(s) and any summary/worklist inside the `with` body;
    the page body that follows sits on the warm page background, so the two zones read as
    distinct. Shared so every page gains the same hierarchy in one place.

    `sticky=True` pins the band to the top of the scroll area (just below the app bar) so
    its controls stay reachable while a long body scrolls."""
    classes = "colophon-toolbar w-full q-gutter-xs q-pa-none"
    if sticky:
        classes += " colophon-toolbar-sticky"
    with ui.column().classes(classes):
        yield


@contextmanager
def page_footer() -> Iterator[ui.column]:
    """A sticky bottom band, the mirror of `page_toolbar(sticky=True)`: it pins page-level
    state to the bottom of the scroll area so it stays visible without scrolling past a long
    body (the Acquire downloads list). Put the footer content inside the `with` body; the
    yielded column is returned so the caller can show/hide the band with `.set_visibility(...)`
    (it starts hidden) and an idle page reclaims the space."""
    with ui.column().classes("colophon-footer w-full q-gutter-xs q-pa-none") as col:
        col.set_visibility(False)  # revealed by the caller once there is something to show
        yield col


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
