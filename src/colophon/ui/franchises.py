"""Manage → Franchises: declare the franchises the library contains so the classifier treats a
matching folder as a franchise instead of inferring an author."""

from __future__ import annotations

from nicegui import ui

from colophon.controller import AppController
from colophon.ui.chrome import page_body, page_header


def render_franchises(controller: AppController) -> None:
    with page_header(controller, "manage", icon="hub"):
        pass

    with page_body("read"):
        ui.label("Franchises").classes("text-h6")
        ui.label("Declared franchises are treated as a franchise tier during a scan, so a "
                 "matching folder is not mistaken for an author.").classes("colophon-muted text-caption")

        listing = ui.column().classes("q-gutter-xs")

        def _refresh() -> None:
            listing.clear()
            with listing:
                names = controller.list_franchises()
                if not names:
                    ui.label("No franchises declared yet.").classes("colophon-muted")
                for name in names:
                    with ui.row().classes("items-center no-wrap"):
                        ui.label(name)
                        ui.button(icon="delete", on_click=lambda n=name: _remove(n)).props(
                            "flat dense round"
                        )

        def _remove(name: str) -> None:
            controller.remove_franchise(name)
            ui.notify(f"Removed franchise '{name}'", type="positive")
            _refresh()

        def _add() -> None:
            name = field.value.strip()
            if not name:
                return
            controller.add_franchise(name)
            field.value = ""
            ui.notify(f"Added franchise '{name}'", type="positive")
            _refresh()

        with ui.row().classes("items-center no-wrap"):
            field = ui.input("Franchise name").props("dense outlined").on("keydown.enter", _add)
            ui.button("Add", icon="add", on_click=_add).props("no-caps")

        _refresh()
