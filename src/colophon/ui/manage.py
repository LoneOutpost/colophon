"""Manage page: list the library-wide vocabulary (authors, narrators, series,
genres, tags) with usage counts and rename / merge / delete entries as undoable
batches."""

from __future__ import annotations

import asyncio
import logging
from urllib.parse import quote

from nicegui import ui

from colophon.controller import AppController
from colophon.core.perf import span
from colophon.services.cleanup import CleanupCandidate, CleanupReport
from colophon.ui.chrome import page_body, page_header, page_section, page_toolbar
from colophon.ui.dialogs import modal
from colophon.ui.filter_input import filter_input

logger = logging.getLogger(__name__)

_PAGE = 100  # vocabulary rows rendered per chunk; the rest fill in via "Show more"

_CLEANUP_DETAIL_CAP = 50  # rows shown per category in the clean-up preview before eliding

_KIND_LABELS = {
    "author": "Authors",
    "narrator": "Narrators",
    "series": "Series",
    "genre": "Genres",
    "tag": "Tags",
    "publisher": "Publisher",
    "language": "Language",
}


def _valid_kind(kind: str | None) -> str:
    """A safe manage vocabulary kind, defaulting to 'author' for anything unrecognized."""
    return kind if kind in _KIND_LABELS else "author"


def _selected_cleanup_ids(report: CleanupReport, checked: set[str]) -> list[str]:
    """The book ids to remove given which category checkboxes are ticked. The two
    categories are disjoint, so this is a simple concatenation in category order."""
    ids: list[str] = []
    if "removed_from_disk" in checked:
        ids += [c.book_id for c in report.removed_from_disk]
    if "outside_scan_paths" in checked:
        ids += [c.book_id for c in report.outside_scan_paths]
    return ids


def _cleanup_dialog(controller: AppController, report: CleanupReport) -> None:
    """Preview modal: per-category counts with expandable detail lists and a
    confirm-to-remove button. Disjoint categories, so counts add up cleanly."""
    groups: list[tuple[str, str, list[CleanupCandidate]]] = [
        ("removed_from_disk", "Entries whose folder was removed from disk",
         report.removed_from_disk),
        ("outside_scan_paths", "Entries no longer under any scan path",
         report.outside_scan_paths),
    ]
    total = sum(len(items) for _key, _label, items in groups)

    with modal() as dialog, ui.card().classes("w-[32rem]"):
        ui.label("Clean up library").classes("text-subtitle1")

        if total == 0:
            ui.label("Nothing to clean up — every entry is accounted for.").classes(
                "text-caption colophon-muted"
            )
            with ui.row().classes("w-full justify-end q-mt-sm"):
                ui.button("Close", on_click=dialog.close).props("flat")
            dialog.open()
            return

        checks: dict[str, ui.checkbox] = {}
        for key, label, items in groups:
            cb = ui.checkbox(f"{label} ({len(items)})").props("dense")
            if not items:
                cb.props("disable")
            checks[key] = cb
            if items:
                with ui.expansion("Show details").props("dense").classes("w-full q-ml-md"):
                    for c in items[:_CLEANUP_DETAIL_CAP]:
                        ui.label(c.title).classes("text-body2")
                        ui.label(str(c.source_folder)).classes("text-caption colophon-muted")
                    if len(items) > _CLEANUP_DETAIL_CAP:
                        ui.label(
                            f"...and {len(items) - _CLEANUP_DETAIL_CAP} more"
                        ).classes("text-caption colophon-muted")

        ui.label(
            "Removed entries lose data that lives only in the app — manual edits, the "
            "chosen cover, chapter edits. A re-scan cannot restore them."
        ).classes("text-caption colophon-muted q-mt-sm")

        async def _confirm() -> None:
            checked = {key for key, cb in checks.items() if cb.value}
            ids = _selected_cleanup_ids(report, checked)
            if not ids:
                return
            remove_btn.props("loading")
            try:
                removed = await asyncio.to_thread(controller.cleanup_remove, ids)
            except Exception:
                logger.exception("clean-up remove failed")
                ui.notify("Clean-up failed (see logs)", type="negative")
                return
            finally:
                remove_btn.props(remove="loading")
            dialog.close()
            ui.notify(f"Removed {removed} " + ("entry" if removed == 1 else "entries"))

        with ui.row().classes("w-full justify-end q-gutter-sm q-mt-sm"):
            ui.button("Cancel", on_click=dialog.close).props("flat")
            remove_btn = ui.button("Remove", icon="delete", on_click=_confirm).props(
                "unelevated color=negative"
            )

        def _sync_enabled() -> None:
            if any(cb.value for cb in checks.values()):
                remove_btn.props(remove="disable")
            else:
                remove_btn.props("disable")

        for cb in checks.values():
            cb.on_value_change(_sync_enabled)
        _sync_enabled()  # start disabled until a box is ticked

    dialog.open()


def _render_utilities(controller: AppController) -> None:
    """Library-wide maintenance actions (the Utilities tab). One-off repairs run on demand — kept
    apart from the vocabulary editing so an action button never sits next to a form's Save."""
    with page_body("read"):
        with page_section(
            "Durations",
            "Re-read length from disk for books that scanned as 0:00 — for example after a download "
            "that was incomplete at scan time has finished. Files with no readable audio are flagged.",
        ):
            reprobe_btn = ui.button("Re-probe durations", icon="timer").props("unelevated")

            async def _reprobe() -> None:
                reprobe_btn.props("loading")
                try:
                    n = await asyncio.to_thread(controller.reprobe_durations)
                except Exception:
                    logger.exception("re-probe durations failed")
                    ui.notify("Re-probe failed (see logs)", type="negative")
                    return
                finally:
                    reprobe_btn.props(remove="loading")
                ui.notify(
                    f"Re-probed durations: updated {n} book(s)" if n
                    else "All readable files already have a duration"
                )

            reprobe_btn.on_click(_reprobe)
            ui.label(
                "Runs in the background — watch the app-bar jobs indicator for progress."
            ).classes("text-caption colophon-muted")

        with page_section(
            "Clean up",
            "Remove library entries whose files are gone — deleted from disk, or no longer "
            "under any scan path. You review the counts and confirm before anything is removed.",
        ):
            cleanup_btn = ui.button("Review clean-up", icon="cleaning_services").props("unelevated")

            async def _review_cleanup() -> None:
                cleanup_btn.props("loading")
                try:
                    report = await asyncio.to_thread(controller.cleanup_report)
                except Exception:
                    logger.exception("clean-up report failed")
                    ui.notify("Could not compute clean-up (see logs)", type="negative")
                    return
                finally:
                    cleanup_btn.props(remove="loading")
                _cleanup_dialog(controller, report)

            cleanup_btn.on_click(_review_cleanup)


def render_manage(controller: AppController, initial_kind: str | None = None,
                  initial_filter: str = "") -> None:
    state: dict[str, object] = {
        "kind": _valid_kind(initial_kind),
        "filter": initial_filter or "",
        "selected": set(),
        "last_batch": None,
    }

    with page_header(controller, "manage", icon="category"):
        pass

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

    async def _maybe_write_tags(res, do_write: bool) -> None:
        """When the 'update file tags' box was checked, rewrite embedded tags on the
        books the operation changed (best-effort, async)."""
        if not do_write or not res.affected_ids:
            return
        books = [b for b in (controller.get_book(i) for i in res.affected_ids) if b is not None]
        if not books:
            return
        ui.notify(f"Writing tags to {len(books)} book(s)...")
        results = await controller.write_tags_books(books)
        ok = sum(1 for r in results if r.ok)
        ui.notify(f"Updated tags on {ok} of {len(books)} book(s)")

    def _write_tags_checkbox():
        return ui.checkbox("Also update file tags").props("dense").classes("q-mt-sm")

    # --- dialogs ---
    def _edit_dialog(name: str) -> None:
        kind = state["kind"]
        with modal() as dialog, ui.card().classes("w-96"):
            ui.label(f"Rename {kind}").classes("text-subtitle1")
            new_in = ui.input("New name", value=name).props("dense autofocus").classes("w-full")
            write_tags = _write_tags_checkbox()

            async def _confirm() -> None:
                new = (new_in.value or "").strip()
                if not new:
                    ui.notify("Enter a name", type="warning")
                    return
                res = controller.rename_catalog_entry(kind, name, new)  # type: ignore[arg-type]
                state["last_batch"] = res.batch_id
                ui.notify(f"Renamed in {res.affected_count} book(s)")
                await _maybe_write_tags(res, write_tags.value)
                dialog.close()
                _selected().clear()
                refresh()

            with ui.row().classes("w-full justify-end q-gutter-sm q-mt-sm"):
                ui.button("Cancel", on_click=dialog.close).props("flat")
                ui.button("Rename", icon="edit", on_click=_confirm).props("unelevated")
        dialog.open()

    def _delete_dialog(name: str, count: int) -> None:
        kind = state["kind"]
        with modal() as dialog, ui.card().classes("w-96"):
            ui.label(f"Delete {kind}").classes("text-subtitle1")
            ui.label(f"Used by {count} books. Remove from all?").classes(
                "text-caption colophon-muted"
            )
            write_tags = _write_tags_checkbox()

            async def _confirm() -> None:
                res = controller.delete_catalog_entry(kind, name)  # type: ignore[arg-type]
                state["last_batch"] = res.batch_id
                ui.notify(f"Removed from {res.affected_count} book(s)")
                await _maybe_write_tags(res, write_tags.value)
                dialog.close()
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
        with modal() as dialog, ui.card().classes("w-96"):
            ui.label(f"Merge {len(sources)} {kind} entries").classes("text-subtitle1")
            ui.label("; ".join(sources)).classes("text-caption colophon-muted")
            target_in = ui.select(
                options=sources,
                label="Merge into",
                new_value_mode="add-unique",
            ).props("dense use-input").classes("w-full")
            write_tags = _write_tags_checkbox()

            async def _confirm() -> None:
                target = (target_in.value or "").strip()
                if not target:
                    ui.notify("Pick or type a target name", type="warning")
                    return
                if len(sources) < 2:
                    ui.notify("Select at least two entries to merge", type="warning")
                    return
                res = controller.merge_catalog_entries(kind, sources, target)  # type: ignore[arg-type]
                state["last_batch"] = res.batch_id
                ui.notify(f"Merged {len(sources)} into {target}")
                await _maybe_write_tags(res, write_tags.value)
                dialog.close()
                _selected().clear()
                refresh()

            with ui.row().classes("w-full justify-end q-gutter-sm q-mt-sm"):
                ui.button("Cancel", on_click=dialog.close).props("flat")
                ui.button("Merge", icon="merge", on_click=_confirm).props("unelevated")
        dialog.open()

    # --- page body: Catalog (vocabulary) and Utilities (maintenance) as tabs ---
    with ui.tabs().props("no-caps inline-label align=left") as top_tabs:
        ui.tab("catalog", label="Catalog", icon="category")
        ui.tab("utilities", label="Utilities", icon="build")
    with ui.tab_panels(top_tabs, value="catalog").classes("w-full").props("keep-alive"):
        with ui.tab_panel("catalog").classes("q-pa-none"):
            with page_toolbar():
                ui.toggle(
                    _KIND_LABELS, value=state["kind"], on_change=lambda e: _on_kind(e.value)
                ).props("no-caps").classes("colophon-seg")
                with ui.row().classes("items-center w-full no-wrap q-gutter-sm"):
                    filter_input(
                        _on_filter, placeholder="Filter", value=str(state["filter"]),
                        aria_label="Filter entries",
                    ).classes("col")
                    merge_btn = ui.button(
                        "Merge selected", icon="merge", on_click=_merge_dialog
                    ).props("flat")
                    undo_btn = ui.button("Undo", icon="undo", on_click=_do_undo).props("flat")
                    ui.button("Franchises", icon="hub",
                              on_click=lambda: ui.navigate.to("/franchises")).props("flat no-caps")
            with page_body("read"):
                list_box = ui.column().classes("w-full gap-0")
        with ui.tab_panel("utilities").classes("q-pa-none"):
            _render_utilities(controller)

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
        with span("manage list render"):
            _refresh()

    def _entry_row(entry) -> None:
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
                    ui.badge(str(entry.count)).props("outline").classes("colophon-chip")
                    ui.button(
                        icon="arrow_outward",
                        on_click=lambda n=entry.name: ui.navigate.to(f"/?filter={quote(n)}"),
                    ).props('flat dense round aria-label="Show in Library"').tooltip("Show books in the Library")
                    ui.button(
                        icon="edit",
                        on_click=lambda n=entry.name: _edit_dialog(n),
                    ).props('flat dense round aria-label="Rename"').tooltip("Rename")
                    ui.button(
                        icon="delete",
                        on_click=lambda n=entry.name, c=entry.count: _delete_dialog(n, c),
                    ).props('flat dense round color=negative aria-label="Remove from all books"').tooltip(
                        "Remove from all books"
                    )

    def _refresh() -> None:
        kind = state["kind"]
        needle = str(state["filter"]).strip().lower()
        entries = controller.catalog_entries(kind)  # type: ignore[arg-type]
        if needle:
            entries = [e for e in entries if needle in e.name.lower()]
        list_box.clear()
        with list_box:
            if not entries:
                ui.label("No entries match" if needle else "No entries").classes(
                    "colophon-muted q-pa-md"
                )
                _sync_buttons()
                return
            # Authors alone can run to thousands of rows; building them all was the whole
            # cost of a manage render (the data behind them is a few ms). Render a chunk and
            # let "Show more" pull the rest, so the initial paint stays flat.
            lst = ui.list().props("separator dense").classes("w-full")
            more_row = ui.row().classes("w-full justify-center q-my-sm")
            rendered = {"n": 0}

            def _render_more() -> None:
                end = min(rendered["n"] + _PAGE, len(entries))
                with lst:
                    for entry in entries[rendered["n"]:end]:
                        _entry_row(entry)
                rendered["n"] = end
                more_row.clear()
                remaining = len(entries) - end
                if remaining:
                    with more_row:
                        ui.button(
                            f"Show more ({remaining} left)", icon="expand_more",
                            on_click=_render_more,
                        ).props("flat no-caps")

            _render_more()
        _sync_buttons()

    refresh()
