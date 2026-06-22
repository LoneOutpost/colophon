"""Manage page: list the library-wide vocabulary (authors, narrators, series,
genres, tags) with usage counts and rename / merge / delete entries as undoable
batches."""

from __future__ import annotations

import logging

from nicegui import ui

from colophon.controller import AppController
from colophon.ui.tabs import app_tabs
from colophon.ui.theme import apply_theme, dark_mode_button, setup_dark_mode

logger = logging.getLogger(__name__)

_KIND_LABELS = {
    "author": "Authors",
    "narrator": "Narrators",
    "series": "Series",
    "genre": "Genres",
    "tag": "Tags",
}


def render_manage(controller: AppController) -> None:
    apply_theme()
    dark = setup_dark_mode()

    state: dict[str, object] = {
        "kind": "author",
        "filter": "",
        "selected": set(),
        "last_batch": None,
    }

    with ui.header(elevated=True).classes("items-center q-px-md"):
        ui.icon("category", color="primary").classes("text-h5")
        ui.label("Colophon").classes("text-h6 q-ml-sm text-weight-medium")
        app_tabs(controller, "manage")
        ui.space()
        dark_mode_button(dark)

    def _selected() -> set[str]:
        return state["selected"]  # type: ignore[return-value]

    def _on_kind(value: str) -> None:
        state["kind"] = value
        _selected().clear()
        refresh()

    def _on_filter(value: str) -> None:
        state["filter"] = value or ""
        refresh()

    def _do_undo() -> None:
        batch_id = state["last_batch"]
        if not batch_id:
            return
        controller.undo(batch_id)  # type: ignore[arg-type]
        state["last_batch"] = None
        _selected().clear()
        ui.notify("Reverted")
        refresh()

    # --- dialogs ---
    def _edit_dialog(name: str) -> None:
        kind = state["kind"]
        with ui.dialog() as dialog, ui.card().classes("w-96"):
            ui.label(f"Rename {kind}").classes("text-subtitle1")
            new_in = ui.input("New name", value=name).props("dense autofocus").classes("w-full")

            def _confirm() -> None:
                new = (new_in.value or "").strip()
                if not new:
                    ui.notify("Enter a name", type="warning")
                    return
                res = controller.rename_catalog_entry(kind, name, new)  # type: ignore[arg-type]
                state["last_batch"] = res.batch_id
                dialog.close()
                ui.notify(f"Renamed in {res.affected_count} book(s)")
                _selected().clear()
                refresh()

            with ui.row().classes("w-full justify-end q-gutter-sm q-mt-sm"):
                ui.button("Cancel", on_click=dialog.close).props("flat")
                ui.button("Rename", icon="edit", on_click=_confirm).props("unelevated")
        dialog.open()

    def _delete_dialog(name: str, count: int) -> None:
        kind = state["kind"]
        with ui.dialog() as dialog, ui.card().classes("w-96"):
            ui.label(f"Delete {kind}").classes("text-subtitle1")
            ui.label(f"Used by {count} books. Remove from all?").classes(
                "text-caption text-grey-7"
            )

            def _confirm() -> None:
                res = controller.delete_catalog_entry(kind, name)  # type: ignore[arg-type]
                state["last_batch"] = res.batch_id
                dialog.close()
                ui.notify(f"Removed from {res.affected_count} book(s)")
                _selected().clear()
                refresh()

            with ui.row().classes("w-full justify-end q-gutter-sm q-mt-sm"):
                ui.button("Cancel", on_click=dialog.close).props("flat")
                ui.button("Remove", icon="delete", on_click=_confirm).props(
                    "unelevated color=negative"
                )
        dialog.open()

    def _merge_dialog() -> None:
        kind = state["kind"]
        sources = sorted(_selected())
        if len(sources) < 2:
            ui.notify("Select at least two entries to merge", type="warning")
            return
        with ui.dialog() as dialog, ui.card().classes("w-96"):
            ui.label(f"Merge {len(sources)} {kind} entries").classes("text-subtitle1")
            ui.label("; ".join(sources)).classes("text-caption text-grey-7")
            target_in = ui.select(
                options=sources,
                label="Merge into",
                new_value_mode="add-unique",
            ).props("dense use-input").classes("w-full")

            def _confirm() -> None:
                target = (target_in.value or "").strip()
                if not target:
                    ui.notify("Pick or type a target name", type="warning")
                    return
                if len(sources) < 2:
                    ui.notify("Select at least two entries to merge", type="warning")
                    return
                res = controller.merge_catalog_entries(kind, sources, target)  # type: ignore[arg-type]
                state["last_batch"] = res.batch_id
                dialog.close()
                ui.notify(f"Merged {len(sources)} into {target}")
                _selected().clear()
                refresh()

            with ui.row().classes("w-full justify-end q-gutter-sm q-mt-sm"):
                ui.button("Cancel", on_click=dialog.close).props("flat")
                ui.button("Merge", icon="merge", on_click=_confirm).props("unelevated")
        dialog.open()

    # --- page body ---
    with ui.column().classes("w-full items-center q-pa-md"):
        with ui.column().classes("w-full gap-3").style("max-width: 760px"):
            ui.toggle(_KIND_LABELS, value="author", on_change=lambda e: _on_kind(e.value)).props(
                "no-caps"
            ).classes("colophon-seg")
            with ui.row().classes("items-center w-full no-wrap q-gutter-sm"):
                ui.input(placeholder="Filter").props("dense clearable outlined").classes(
                    "col"
                ).on_value_change(lambda e: _on_filter(e.value))
                merge_btn = ui.button(
                    "Merge selected", icon="merge", on_click=_merge_dialog
                ).props("flat")
                undo_btn = ui.button("Undo", icon="undo", on_click=_do_undo).props("flat")

            list_box = ui.column().classes("w-full gap-0")

    def _sync_buttons() -> None:
        merge_btn.set_enabled(len(_selected()) >= 2)
        undo_btn.set_enabled(state["last_batch"] is not None)

    def _toggle_select(name: str, on: bool) -> None:
        if on:
            _selected().add(name)
        else:
            _selected().discard(name)
        _sync_buttons()

    def refresh() -> None:
        kind = state["kind"]
        needle = str(state["filter"]).strip().lower()
        entries = controller.catalog_entries(kind)  # type: ignore[arg-type]
        if needle:
            entries = [e for e in entries if needle in e.name.lower()]
        list_box.clear()
        with list_box:
            if not entries:
                ui.label("No entries match" if needle else "No entries").classes(
                    "text-grey-6 q-pa-md"
                )
            else:
                with ui.list().props("separator dense").classes("w-full"):
                    for entry in entries:
                        with ui.item():
                            with ui.item_section().props("avatar"):
                                ui.checkbox(
                                    value=entry.name in _selected(),
                                    on_change=lambda e, n=entry.name: _toggle_select(n, e.value),
                                ).props("dense")
                            with ui.item_section():
                                ui.item_label(entry.name)
                            with ui.item_section().props("side"):
                                with ui.row().classes("items-center no-wrap q-gutter-xs"):
                                    ui.badge(str(entry.count)).props("color=grey-6 outline")
                                    ui.button(
                                        icon="edit",
                                        on_click=lambda n=entry.name: _edit_dialog(n),
                                    ).props("flat dense round").tooltip("Rename")
                                    ui.button(
                                        icon="delete",
                                        on_click=lambda n=entry.name, c=entry.count: _delete_dialog(
                                            n, c
                                        ),
                                    ).props("flat dense round color=negative").tooltip(
                                        "Remove from all books"
                                    )
        _sync_buttons()

    refresh()
