"""Workspace page: an application shell with a navigation drawer, a book list,
and a detail pane.

Built from Quasar/NiceGUI structural components (header, drawer, cards, lists)
rather than bare containers, so it reads as an application: elevated header,
bordered drawer, carded panels with separators and list items. Follows the
project polish rules: real icons (no emoji), a loading state on every async
action, no dead controls, and a consistent spacing scale.
"""

from __future__ import annotations

import asyncio
import logging
from pathlib import Path

from nicegui import ui

from colophon.controller import AppController
from colophon.core.chapters import file_boundary_chapters
from colophon.core.fields import EDITABLE_FIELDS, field_provenance, get_field
from colophon.core.models import BookUnit
from colophon.core.normalize import normalize_description, normalize_text

logger = logging.getLogger(__name__)

# Height of the carded content area, leaving room for the app header and footer.
_CONTENT_HEIGHT = "calc(100vh - 136px)"

# Sentinel marking a bulk-edit field whose selected books hold differing values.
_MIXED = object()

# Status-bar state badges: (BookState value, short label, color). Shown only when count > 0.
_STATUS_BADGES = [
    ("detected", "Detected", "grey-6"),
    ("needs_review", "Needs review", "warning"),
    ("ready", "Ready", "positive"),
    ("organized", "Organized", "info"),
    ("failed", "Failed", "negative"),
]


def _fmt_duration(seconds: float) -> str:
    """Format a file length as hours and minutes, e.g. '1h 2m' or '47m'."""
    minutes = round(seconds / 60)
    hours, mins = divmod(minutes, 60)
    return f"{hours}h {mins}m" if hours else f"{mins}m"


def _confidence_color(value: float) -> str:
    if value >= 75:
        return "positive"
    if value >= 40:
        return "warning"
    return "negative"


def render_workspace(controller: AppController) -> None:
    selected_ids: set[str] = set()
    scope: dict[str, object] = {"kind": "all", "key": None}
    foster_selected: set[Path] = set()
    view: dict[str, object] = {
        "mode": "library", "cwd": None, "multiselect": False, "group_by": "author",
    }

    def _scan_roots() -> list[Path]:
        return list(controller.ctx.config.scan_paths)

    def _selected_books() -> list:
        return [b for b in (controller.get_book(i) for i in selected_ids) if b is not None]

    def _books_for_scope() -> list:
        tree = controller.library_tree()
        kind, key = scope["kind"], scope["key"]
        if kind == "needs_id":
            return list(tree.needs_id)
        if kind == "author":
            node = next((a for a in tree.authors if a.name == key), None)
            if node is None:
                return []
            return [b for s in node.series for b in s.books] + node.standalone
        all_books = list(tree.needs_id)
        for a in tree.authors:
            all_books += [b for s in a.series for b in s.books] + a.standalone
        if kind == "folder" and key:
            folder = Path(str(key))
            return [
                b for b in all_books
                if b.source_folder == folder or folder in b.source_folder.parents
            ]
        if kind == "series" and key:
            return [b for b in all_books if b.series and b.series[0].name == key]
        return all_books

    # --- detail pane ---
    def show_detail(book_id: str) -> None:
        detail_container.clear()
        book = controller.get_book(book_id)
        with detail_container:
            if book is None:
                with ui.column().classes("w-full items-center q-pa-lg"):
                    ui.icon("menu_book").classes("text-h3 text-grey-5")
                    ui.label("Select a book to see its details").classes("text-grey-6")
                return

            with ui.row().classes("items-center w-full"):
                ui.label(book.title or "(untitled)").classes("text-h6")
                ui.space()
                ui.badge(f"{book.confidence:.0f}").props(f"color={_confidence_color(book.confidence)}")
                ui.label(book.state.value).classes("text-caption text-grey-7 q-ml-sm")
            if book.confidence_signals:
                with ui.row().classes("items-center w-full q-gutter-xs"):
                    for sig in book.confidence_signals:
                        color = "positive" if sig.points >= 0 else "negative"
                        ui.badge(f"{sig.name.replace('_', ' ')} {sig.points:+d}").props(
                            f"color={color} outline"
                        ).tooltip(sig.detail)
            ui.separator().classes("q-my-sm")

            # editable fields, each prefilled with its value + provenance badge
            inputs: dict[str, ui.input | ui.textarea] = {}
            originals: dict[str, str] = {}
            for field in EDITABLE_FIELDS:
                value = get_field(book, field) or ""
                originals[field] = value
                with ui.row().classes("items-center w-full no-wrap q-gutter-xs"):
                    if field == "description":
                        inp = ui.textarea(field, value=value).props("dense").classes("col")
                    else:
                        inp = ui.input(field, value=value).props("dense").classes("col")
                    inputs[field] = inp
                    normalizer = normalize_description if field == "description" else normalize_text
                    ui.button(
                        icon="auto_fix_high",
                        on_click=lambda inp=inp, fn=normalizer: inp.set_value(fn(inp.value or "")),
                    ).props("flat dense round").classes("self-center").tooltip("Normalize")
                    source = field_provenance(book, field)
                    if source:
                        ui.badge(source).props("color=grey-6 outline").classes("self-center")

            def _save(b=book) -> None:
                changed = {
                    f: (inputs[f].value or None)
                    for f in EDITABLE_FIELDS
                    if (inputs[f].value or "") != originals[f]
                }
                if not changed:
                    ui.notify("No changes")
                    return
                controller.save_fields(b, changed)
                ui.notify("Saved")
                refresh_list()
                show_detail(b.id)

            async def _compare(b=book) -> None:
                with ui.dialog() as dialog, ui.card().classes("w-96"):
                    ui.label(f"Matches for {b.title or '(untitled)'}").classes("text-subtitle1")
                    body = ui.column().classes("w-full")
                    with body:
                        ui.spinner()
                    try:
                        matches = await controller.get_matches(b)
                    except Exception:
                        logger.exception("get_matches failed")
                        matches = []

                    field_labels = {
                        "title": "Title", "author": "Author", "narrator": "Narrator",
                        "series": "Series", "sequence": "Sequence", "year": "Year",
                        "asin": "ASIN", "description": "Description",
                    }

                    def show_candidates() -> None:
                        body.clear()
                        with body:
                            if not matches:
                                ui.label("No matches found").classes("text-grey-6")
                            for m in matches[:10]:
                                authors = ", ".join(m.authors) or "unknown"
                                year = f" ({m.publish_year})" if m.publish_year else ""
                                with ui.item(on_click=lambda result=m: show_picker(result)).props("clickable"):
                                    with ui.item_section():
                                        ui.item_label(m.title or "?")
                                        ui.item_label(f"{m.provider} · {authors}{year}").props("caption")

                    def show_picker(result) -> None:
                        body.clear()
                        checks: dict[str, ui.checkbox] = {}
                        with body:
                            ui.button("Back to matches", icon="arrow_back", on_click=show_candidates).props(
                                "flat dense no-caps"
                            )
                            with ui.scroll_area().classes("w-full").style("max-height: 45vh"):
                                with ui.list().props("dense").classes("w-full"):
                                    for key, source in controller.match_field_values(result).items():
                                        current = get_field(b, key)
                                        with ui.item():
                                            with ui.item_section().props("avatar"):
                                                checks[key] = ui.checkbox(value=(source != (current or None)))
                                            with ui.item_section():
                                                ui.item_label(f"{field_labels.get(key, key)}: {source}")
                                                ui.item_label(f"current: {current or '(none)'}").props("caption")
                                    if result.cover_url:
                                        with ui.item():
                                            with ui.item_section().props("avatar"):
                                                checks["cover"] = ui.checkbox(value=(result.cover_url != b.cover_url))
                                            with ui.item_section():
                                                ui.item_label("Cover art")
                                                ui.item_label(result.cover_url).props("caption")

                            def _apply(res=result) -> None:
                                selected = {k for k, c in checks.items() if c.value}
                                if not selected:
                                    ui.notify("No fields selected")
                                    return
                                controller.apply_match_fields(b, res, selected)
                                dialog.close()
                                ui.notify(f"Applied {len(selected)} field(s) from {res.provider}")
                                refresh_list()
                                show_detail(b.id)

                            with ui.row().classes("w-full justify-end q-gutter-sm q-mt-sm"):
                                ui.button("Cancel", on_click=dialog.close).props("flat")
                                ui.button("Apply selected", icon="done_all", on_click=_apply)

                    show_candidates()
                dialog.open()

            async def _tag_dialog(b=book) -> None:
                plan = controller.tag_plan(b)
                with ui.dialog() as dialog, ui.card().classes("w-96"):
                    ui.label(f"Write tags to {len(plan.files)} file(s)").classes("text-subtitle1")
                    for warning in plan.warnings:
                        with ui.row().classes("items-center no-wrap"):
                            ui.icon("warning", color="warning")
                            ui.label(warning).classes("text-caption text-warning")
                    if plan.embed_cover:
                        ui.label("Cover art will be embedded.").classes("text-caption text-grey-7")
                    with ui.scroll_area().classes("w-full").style("max-height: 40vh"):
                        with ui.list().props("dense").classes("w-full"):
                            for fp in plan.files:
                                with ui.item():
                                    with ui.item_section():
                                        ui.item_label(fp.path.name)
                                        ui.item_label(", ".join(fp.changed_fields) or "no changes").props(
                                            "caption"
                                        )
                    actions = ui.row().classes("w-full justify-end q-gutter-sm q-mt-sm")
                    with actions:
                        ui.button("Cancel", on_click=dialog.close).props("flat")
                        commit_btn = ui.button("Write tags", icon="sell")

                    async def _commit() -> None:
                        commit_btn.props("loading=true")
                        try:
                            result = await controller.write_tags(b)
                        finally:
                            commit_btn.props(remove="loading")
                        actions.clear()
                        with actions:
                            note = f"Wrote {result.written} file(s)" + (
                                f", {result.failed} failed" if result.failed else ""
                            )
                            ui.label(note).classes("text-caption q-mr-auto self-center")
                            ui.button(
                                "Undo",
                                icon="undo",
                                on_click=lambda: (
                                    controller.undo_tag_batch(),
                                    ui.notify("Reverted tag write (embedded cover kept)"),
                                    dialog.close(),
                                ),
                            ).props("flat")
                            ui.button("Close", on_click=dialog.close).props("flat")
                        refresh_list()
                        refresh_status()

                    commit_btn.on_click(_commit)
                dialog.open()

            def _remap_dialog(b=book) -> None:
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
                        controller.remap(b, src=src.value, dst=dst.value, clear_source=clear.value)
                        dialog.close()
                        ui.notify(f"Remapped {src.value} to {dst.value}")
                        refresh_list()
                        show_detail(b.id)

                    with ui.row().classes("w-full justify-end q-gutter-sm q-mt-sm"):
                        ui.button("Cancel", on_click=dialog.close).props("flat")
                        ui.button("Remap", icon="swap_horiz", on_click=_apply)
                dialog.open()

            with ui.row().classes("q-gutter-sm q-mt-sm"):
                ui.button("Save", icon="save", on_click=_save)
                ui.button("Compare matches", icon="search", on_click=_compare).props("outline")
                ui.button("Write tags", icon="sell", on_click=lambda b=book: _tag_dialog(b)).props("outline")
                ui.button("Remap", icon="swap_horiz", on_click=lambda b=book: _remap_dialog(b)).props("flat")
                ui.button(
                    "Mark ready",
                    icon="check",
                    on_click=lambda b=book: (controller.mark_ready(b), ui.notify("Marked ready"), refresh_list()),
                ).props("flat")

            if book.source_files:
                ui.separator().classes("q-my-sm")
                ui.label(f"Files ({len(book.source_files)})").classes("text-subtitle2")

                def _rename_dialog(sf_path: Path, b: BookUnit = book) -> None:
                    with ui.dialog() as dialog, ui.card():
                        ui.label("Rename file").classes("text-subtitle1")
                        name_input = ui.input("New filename", value=sf_path.name).classes("w-72")

                        def _do_rename() -> None:
                            if controller.rename_file(b, sf_path, name_input.value.strip()):
                                ui.notify("Renamed")
                            else:
                                ui.notify("Rename failed (name in use?)", type="negative")
                            dialog.close()
                            show_detail(b.id)

                        with ui.row():
                            ui.button("Rename", on_click=_do_rename)
                            ui.button("Cancel", on_click=dialog.close).props("flat")
                    dialog.open()

                with ui.list().props("dense bordered").classes("w-full"):
                    for idx, sf in enumerate(book.source_files):
                        with ui.item():
                            with ui.item_section():
                                ui.item_label(sf.path.name)
                                ui.item_label(_fmt_duration(sf.duration_seconds)).props("caption")
                            with ui.item_section().props("side"):
                                with ui.row().classes("q-gutter-xs no-wrap"):
                                    ui.button(icon="arrow_upward", on_click=lambda p=sf.path: (controller.move_file(book, p, -1), show_detail(book.id))).props("flat dense round").set_enabled(idx > 0)
                                    ui.button(icon="arrow_downward", on_click=lambda p=sf.path: (controller.move_file(book, p, 1), show_detail(book.id))).props("flat dense round").set_enabled(idx < len(book.source_files) - 1)
                                    ui.button(icon="edit", on_click=lambda p=sf.path: _rename_dialog(p)).props("flat dense round")
                                    ui.button(icon="remove_circle_outline", on_click=lambda p=sf.path: (controller.exclude_file(book, p), ui.notify("Excluded"), show_detail(book.id))).props("flat dense round color=negative")

                # chapter preview (read-only) reflecting current file order
                chapters = file_boundary_chapters(
                    [(sf.path.name, sf.duration_seconds) for sf in book.source_files]
                )
                ui.label(f"Chapters ({len(chapters)})").classes("text-subtitle2 q-mt-sm")
                with ui.list().props("dense").classes("w-full"):
                    for n, ch in enumerate(chapters, start=1):
                        with ui.item():
                            with ui.item_section():
                                ui.item_label(f"{n}. {ch.title}")
                            with ui.item_section().props("side"):
                                _t = ch.start_ms // 1000
                                ui.item_label(
                                    f"{_t // 3600}:{(_t % 3600) // 60:02d}:{_t % 60:02d}"
                                ).props("caption")

    # --- bulk editor (shown when 2+ books are selected) ---
    def show_bulk() -> None:
        detail_container.clear()
        books = _selected_books()
        with detail_container:
            with ui.row().classes("items-center w-full"):
                ui.icon("edit_note").classes("text-h6")
                ui.label(f"Editing {len(books)} books").classes("text-h6 q-ml-xs")
            ui.label("Blank fields are left unchanged.").classes("text-caption text-grey-6")
            ui.separator().classes("q-my-sm")

            inputs: dict[str, ui.input | ui.textarea] = {}
            originals: dict[str, object] = {}
            for field in EDITABLE_FIELDS:
                values = {(get_field(b, field) or "") for b in books}
                mixed = len(values) > 1
                common = "" if mixed else next(iter(values), "")
                originals[field] = _MIXED if mixed else common
                with ui.row().classes("items-center w-full no-wrap q-gutter-xs"):
                    if field == "description":
                        inp = ui.textarea(field, value=common).props("dense").classes("col")
                    else:
                        inp = ui.input(field, value=common).props("dense").classes("col")
                    if mixed:
                        inp.props('placeholder="(multiple values)"')
                    inputs[field] = inp

            def _apply_bulk() -> None:
                changed: dict[str, str | None] = {}
                for field, inp in inputs.items():
                    current = inp.value or ""
                    original = originals[field]
                    if original is _MIXED:
                        if current:  # only touch a mixed field if the user typed something
                            changed[field] = current
                    elif current != original:
                        changed[field] = current or None
                if not changed:
                    ui.notify("No changes")
                    return
                for field, value in changed.items():
                    controller.bulk_edit(books, field, value)
                ui.notify(f"Updated {len(changed)} field(s) on {len(books)} books")
                refresh_list()
                refresh_status()
                show_bulk()

            async def _bulk_tag_dialog() -> None:
                plans = [(b, controller.tag_plan(b)) for b in books]
                total_files = sum(len(p.files) for _, p in plans)
                with ui.dialog() as dialog, ui.card().classes("w-96"):
                    ui.label(f"Write tags to {len(books)} books ({total_files} files)").classes(
                        "text-subtitle1"
                    )
                    with ui.scroll_area().classes("w-full").style("max-height: 40vh"):
                        with ui.list().props("dense").classes("w-full"):
                            for b, plan in plans:
                                with ui.item():
                                    with ui.item_section():
                                        ui.item_label(b.title or "(untitled)")
                                        note = f"{len(plan.files)} file(s)" + (
                                            f" · {len(plan.warnings)} warning(s)" if plan.warnings else ""
                                        )
                                        ui.item_label(note).props("caption")
                    actions = ui.row().classes("w-full justify-end q-gutter-sm q-mt-sm")
                    with actions:
                        ui.button("Cancel", on_click=dialog.close).props("flat")
                        commit_btn = ui.button("Write tags", icon="sell")

                    async def _commit() -> None:
                        commit_btn.props("loading=true")
                        try:
                            results = await controller.write_tags_books(books)
                        finally:
                            commit_btn.props(remove="loading")
                        wrote = sum(r.written for r in results)
                        failed = sum(r.failed for r in results)
                        actions.clear()
                        with actions:
                            note = f"Wrote {wrote} file(s) across {len(books)} books" + (
                                f", {failed} failed" if failed else ""
                            )
                            ui.label(note).classes("text-caption q-mr-auto self-center")
                            ui.button(
                                "Undo",
                                icon="undo",
                                on_click=lambda: (
                                    controller.undo_tag_batch(),
                                    ui.notify("Reverted tag write (embedded covers kept)"),
                                    dialog.close(),
                                ),
                            ).props("flat")
                            ui.button("Close", on_click=dialog.close).props("flat")
                        refresh_list()
                        refresh_status()

                    commit_btn.on_click(_commit)
                dialog.open()

            with ui.row().classes("q-gutter-sm q-mt-sm"):
                ui.button("Apply to selection", icon="done_all", on_click=_apply_bulk)
                ui.button("Write tags", icon="sell", on_click=_bulk_tag_dialog).props("outline")
                ui.button(
                    "Clear selection",
                    icon="clear",
                    on_click=lambda: (
                        selected_ids.clear(),
                        refresh_list(),
                        refresh_status(),
                        show_detail(""),
                    ),
                ).props("flat")

    # --- book list ---
    def _select_all(book_ids: list[str]) -> None:
        selected_ids.update(book_ids)
        refresh_nav()  # multiselect checkboxes live in the navigator now
        refresh_status()
        _after_select()

    def _deselect_all() -> None:
        selected_ids.clear()
        refresh_nav()
        refresh_status()
        _after_select()

    def refresh_list() -> None:
        list_container.clear()
        books = _books_for_scope()
        with list_container:
            if not books:
                ui.label("No books in this view").classes("text-grey-6 q-pa-md")
                return
            # Selection is driven from the navigator (multiselect checkboxes on the
            # author/series entries), so the book list is plain click-to-view here.
            with ui.list().props("separator dense").classes("w-full"):
                for book in books:
                    with ui.item(on_click=lambda bid=book.id: show_detail(bid)).props("clickable"):
                        with ui.item_section():
                            ui.item_label(book.title or "(untitled)")
                            ui.item_label(", ".join(book.authors) or "unknown author").props("caption")
                        with ui.item_section().props("side"):
                            ui.badge(f"{book.confidence:.0f}").props(
                                f"color={_confidence_color(book.confidence)}"
                            )

    # --- folder browser (Folders mode) ---
    def _toggle_foster(path: Path, on: bool) -> None:
        if on:
            foster_selected.add(path)
        else:
            foster_selected.discard(path)

    def _foster_dialog() -> None:
        paths = sorted(foster_selected)
        if not paths:
            ui.notify("No files selected")
            return
        with ui.dialog() as dialog, ui.card().classes("w-96"):
            ui.label(f"Foster {len(paths)} file(s)?").classes("text-subtitle1")
            ui.label(
                "Each selected file moves into a new subfolder named after the file, so it "
                "scans as its own book. The affected folders are then rescanned."
            ).classes("text-caption text-grey-6")
            body = ui.column().classes("w-full")
            with body, ui.scroll_area().classes("w-full").style("max-height: 35vh"):
                with ui.list().props("dense").classes("w-full"):
                    for p in paths:
                        with ui.item():
                            with ui.item_section():
                                ui.item_label(p.name)
                                ui.item_label(f"into {p.stem}/").props("caption")
            actions = ui.row().classes("w-full justify-end q-gutter-sm q-mt-sm")
            with actions:
                ui.button("Cancel", on_click=dialog.close).props("flat")
                confirm = ui.button("Foster", icon="subdirectory_arrow_right")

            async def _commit() -> None:
                # foster_files does disk renames + a subtree rescan (slow over network
                # mounts), so run it off the event loop and show a busy state.
                confirm.props("loading=true")
                try:
                    results = await asyncio.to_thread(controller.foster_files, paths)
                finally:
                    confirm.props(remove="loading")
                ok = sum(1 for r in results if r.ok)
                failures = [r for r in results if not r.ok]
                foster_selected.clear()
                body.clear()
                with body:
                    ui.label(f"Fostered {ok} of {len(results)} file(s).").classes("text-body2")
                    if failures:
                        ui.label("Failed:").classes("text-caption text-negative q-mt-xs")
                        with ui.list().props("dense").classes("w-full"):
                            for r in failures:
                                with ui.item(), ui.item_section():
                                    ui.item_label(r.source.name)
                                    ui.item_label(r.error or "unknown error").props("caption")

                def _close_and_refresh() -> None:
                    # Refresh AFTER closing: _render_middle rebuilds the folder browser
                    # (which hosts this dialog's trigger), so refreshing while the dialog
                    # is open would tear it down before the result is read.
                    dialog.close()
                    refresh_nav()
                    _render_middle()

                actions.clear()
                with actions:
                    ui.button("Close", on_click=_close_and_refresh).props("flat")

            confirm.on_click(_commit)
        dialog.open()

    def refresh_folders() -> None:
        list_container.clear()
        roots = _scan_roots()
        cwd = view["cwd"]
        if cwd is None and roots:
            cwd = roots[0]
            view["cwd"] = cwd
        with list_container:
            if not roots:
                ui.label("No scan paths configured. Set them in Settings.").classes(
                    "text-grey-6 q-pa-md"
                )
                return
            cwd = Path(str(cwd))
            if len(roots) > 1:
                # An "All scan paths" entry resets the Library view to all books.
                options = {"__all__": "All scan paths"}
                options.update({str(r): (r.name or str(r)) for r in roots})
                browse_root = next(
                    (str(r) for r in roots if cwd == r or r in cwd.parents), str(roots[0])
                )
                ui.select(
                    options,
                    value="__all__" if scope["kind"] == "all" else browse_root,
                    on_change=lambda e: _select_root(e.value),
                ).props("dense outlined").classes("w-full q-mb-sm")

            with ui.row().classes("items-center w-full no-wrap q-gutter-xs q-mb-xs"):
                ui.icon("folder_open").classes("text-grey-7")
                ui.label(str(cwd)).classes("text-caption text-grey-7 ellipsis col")
                ui.button(
                    "Foster selected", icon="subdirectory_arrow_right", on_click=_foster_dialog
                ).props("dense color=primary")

            listing = controller.list_directory(cwd)
            with ui.list().props("dense bordered").classes("w-full"):
                # "Up" entry: hidden once cwd is a configured scan root, so normal
                # browsing stops at the root (nested roots are not specially handled).
                if cwd not in {Path(str(r)) for r in roots}:
                    with ui.item(on_click=lambda p=cwd.parent: _browse_to(p)).props("clickable"):
                        with ui.item_section().props("avatar"):
                            ui.icon("arrow_upward")
                        with ui.item_section():
                            ui.item_label("..")
                    ui.separator()
                if not listing.entries:
                    with ui.item():
                        with ui.item_section():
                            ui.item_label("(empty)").classes("text-grey-6")
                for entry in listing.entries:
                    if entry.is_dir:
                        with ui.item(on_click=lambda p=entry.path: _browse_to(p)).props("clickable"):
                            with ui.item_section().props("avatar"):
                                ui.icon("folder", color="amber-7")
                            with ui.item_section():
                                ui.item_label(entry.name)
                    elif entry.is_audio:
                        with ui.item():
                            with ui.item_section().props("avatar"):
                                ui.checkbox(
                                    value=entry.path in foster_selected,
                                    on_change=lambda e, p=entry.path: _toggle_foster(p, e.value),
                                )
                            with ui.item_section().props("avatar"):
                                ui.icon("audiotrack", color="primary")
                            with ui.item_section():
                                ui.item_label(entry.name)
                    else:
                        with ui.item().props("disable"):
                            with ui.item_section().props("avatar"):
                                ui.icon("insert_drive_file", color="grey-5")
                            with ui.item_section():
                                ui.item_label(entry.name).classes("text-grey-5")

    # --- navigator ---
    def _nav_item(
        label: str, icon: str, active: bool, on_click, color: str | None = None, *, checkbox=None
    ) -> None:
        with ui.item(on_click=on_click).props("clickable" + (" active" if active else "")):
            if checkbox is not None:
                checked, on_change = checkbox
                with ui.item_section().props("avatar"):
                    ui.checkbox(value=checked, on_change=on_change)
            with ui.item_section().props("avatar"):
                ui.icon(icon, color=color) if color else ui.icon(icon)
            with ui.item_section():
                ui.item_label(label)

    def refresh_nav() -> None:
        nav_container.clear()
        tree = controller.library_tree()
        kind, key = scope["kind"], scope["key"]
        with nav_container:
            ui.toggle(
                {"library": "Library", "folders": "Folders"},
                value=view["mode"],
                on_change=lambda e: _set_mode(e.value),
            ).props("dense no-caps").classes("w-full q-mb-sm")
            if view["mode"] == "folders":
                ui.label(
                    "Browse scan folders and foster loose files into their own subfolders."
                ).classes("text-caption text-grey-6")
                return
            ui.switch(
                "Multiselect", value=view["multiselect"], on_change=lambda e: _set_multiselect(e.value)
            ).props("dense").classes("q-mb-sm")
            ui.toggle(
                {"author": "By author", "series": "By series"},
                value=view["group_by"],
                on_change=lambda e: _set_group_by(e.value),
            ).props("dense no-caps").classes("w-full q-mb-sm")
            multiselect = bool(view["multiselect"])

            def _node_checkbox(book_ids: list[str]):
                # (checked, on_change) for a navigator entry when multiselect is on;
                # None otherwise. Checked when all of the node's books are selected.
                if not multiselect:
                    return None
                checked = bool(book_ids) and all(i in selected_ids for i in book_ids)
                return (checked, lambda e, ids=book_ids: _toggle_node(ids, e.value))

            if multiselect:
                with ui.row().classes("items-center q-gutter-xs q-pb-xs"):
                    ui.button(
                        "Select all", icon="done_all",
                        on_click=lambda: _select_all([b.id for b in _books_for_scope()]),
                    ).props("flat dense no-caps")
                    ui.button("Deselect all", icon="remove_done", on_click=_deselect_all).props(
                        "flat dense no-caps"
                    ).set_enabled(bool(selected_ids))
            with ui.list().props("dense").classes("w-full"):
                _nav_item("All books", "library_books", kind == "all", lambda: _set_scope("all", None))
                if tree.needs_id:
                    _nav_item(
                        f"Needs identification ({len(tree.needs_id)})",
                        "help_outline",
                        kind == "needs_id",
                        lambda: _set_scope("needs_id", None),
                        color="negative",
                        checkbox=_node_checkbox([b.id for b in tree.needs_id]),
                    )
                if view["group_by"] == "series":
                    series_names = sorted({s.name for a in tree.authors for s in a.series})
                    for name in series_names:
                        sids = [b.id for a in tree.authors for s in a.series if s.name == name for b in s.books]
                        _nav_item(
                            name,
                            "collections_bookmark",
                            kind == "series" and key == name,
                            lambda n=name: _set_scope("series", n),
                            checkbox=_node_checkbox(sids),
                        )
                else:
                    for author in tree.authors:
                        aids = [b.id for s in author.series for b in s.books] + [
                            b.id for b in author.standalone
                        ]
                        _nav_item(
                            author.name,
                            "person",
                            kind == "author" and key == author.name,
                            lambda name=author.name: _set_scope("author", name),
                            checkbox=_node_checkbox(aids),
                        )

    def _render_middle() -> None:
        middle_title.text = "Folder contents" if view["mode"] == "folders" else "Books"
        if view["mode"] == "folders":
            refresh_folders()
        else:
            refresh_list()

    def _set_mode(mode: str) -> None:
        view["mode"] = mode
        refresh_nav()
        _render_middle()

    def _set_group_by(value: str) -> None:
        view["group_by"] = value
        scope["kind"], scope["key"] = "all", None  # reset scope when switching grouping
        refresh_nav()
        _render_middle()

    def _set_multiselect(on: bool) -> None:
        view["multiselect"] = on
        if not on:
            selected_ids.clear()  # leaving multiselect drops the selection
        refresh_nav()
        refresh_list()
        refresh_status()
        _after_select()

    def _set_scope(kind: str, key) -> None:
        scope["kind"], scope["key"] = kind, key
        refresh_nav()
        _render_middle()

    def _browse_to(folder: Path) -> None:
        # Navigating the folder browser silently filters the Library view to this
        # folder (visible when you switch to Library mode); no extra nav entry.
        view["cwd"] = folder
        scope["kind"], scope["key"] = "folder", str(folder)
        refresh_folders()

    def _select_root(value: str) -> None:
        if value == "__all__":
            scope["kind"], scope["key"] = "all", None  # reset Library to all scan paths
            roots = _scan_roots()
            if roots:
                view["cwd"] = roots[0]
            refresh_folders()
        else:
            _browse_to(Path(value))

    def _after_select() -> None:
        n = len(selected_ids)
        if n >= 2:
            show_bulk()
        elif n == 1:
            show_detail(next(iter(selected_ids)))
        else:
            show_detail("")

    def _toggle_node(book_ids: list[str], on: bool) -> None:
        # Multiselect operates on navigator entries (authors/series): checking a
        # node selects all of its books for bulk actions.
        if on:
            selected_ids.update(book_ids)
        else:
            selected_ids.difference_update(book_ids)
        _after_select()
        refresh_status()

    def _undo() -> None:
        if controller.undo_last():
            ui.notify("Undid last change")
        else:
            ui.notify("Nothing to undo")
        _refresh_all()

    def refresh_status() -> None:
        status_container.clear()
        stats = controller.dashboard_stats()
        with status_container:
            ui.icon("library_books").classes("text-grey-7")
            ui.label(f"{stats.get('total', 0)} books").classes("text-caption")
            for state, label, color in _STATUS_BADGES:
                count = stats.get(state, 0)
                if count:
                    ui.badge(f"{label} {count}").props(f"color={color}")
            ui.space()
            if selected_ids:
                ui.label(f"{len(selected_ids)} selected").classes("text-caption text-grey-7")
            ui.button("Undo", icon="undo", on_click=_undo).props("flat dense")

    def _refresh_all() -> None:
        refresh_nav()
        _render_middle()
        refresh_status()
        # Keep an open bulk editor truthful after a global refresh (undo, scan, etc.).
        if len(selected_ids) >= 2:
            show_bulk()

    # --- async actions ---
    async def _run(button, action, done_msg: str) -> None:
        button.props("loading=true")
        try:
            await action()
            ui.notify(done_msg)
        except Exception:
            logger.exception("workspace action failed")
            ui.notify("Action failed (see logs)", type="negative")
        finally:
            button.props(remove="loading")
            _refresh_all()

    async def _scan() -> None:
        n = await asyncio.to_thread(controller.scan)
        ui.notify(f"Scanned {n} book units")

    async def _identify() -> None:
        await controller.identify_pending()

    async def _process() -> None:
        books = _selected_books() or controller.ready_books()
        if not books:
            ui.notify("Nothing selected or ready")
            return
        for book in books:
            await asyncio.to_thread(controller.process_one, book, confirm_delete=False)
        selected_ids.clear()
        if await controller.trigger_abs_scan():
            ui.notify("Triggered AudiobookShelf rescan")

    # --- application shell ---
    with ui.header(elevated=True).classes("items-center q-px-md"):
        ui.icon("auto_stories").classes("text-h5")
        ui.label("Colophon").classes("text-h6 q-ml-sm")
        ui.space()
        scan_btn = ui.button("Scan", icon="search").props("flat color=white")
        identify_btn = ui.button("Identify", icon="travel_explore").props("flat color=white")
        process_btn = ui.button("Encode + organize", icon="play_arrow").props("flat color=white")
        ui.button(icon="settings", on_click=lambda: ui.navigate.to("/settings")).props(
            "flat round color=white"
        ).tooltip("Settings")

    scan_btn.on_click(lambda: _run(scan_btn, _scan, "Scan complete"))
    identify_btn.on_click(lambda: _run(identify_btn, _identify, "Identification complete"))
    process_btn.on_click(lambda: _run(process_btn, _process, "Processing complete"))

    # The navigator is an in-content card rather than ui.left_drawer: the drawer
    # syncs its open state with a JavaScript round-trip on connect (1.0s timeout)
    # which fails over remote/high-latency connections. A card avoids that.
    with ui.row().classes("w-full no-wrap q-gutter-md q-pa-md items-stretch").style(
        f"height: {_CONTENT_HEIGHT}"
    ):
        with ui.card().classes("column").style("width: 260px; height: 100%"):
            ui.label("Library").classes("text-subtitle1")
            ui.separator()
            with ui.scroll_area().classes("col"):
                nav_container = ui.column().classes("w-full gap-0")
        with ui.card().classes("col-5 column").style("height: 100%"):
            middle_title = ui.label("Books").classes("text-subtitle1")
            ui.separator()
            with ui.scroll_area().classes("col"):
                list_container = ui.column().classes("w-full gap-0")
        with ui.card().classes("col column").style("height: 100%"):
            ui.label("Details").classes("text-subtitle1")
            ui.separator()
            with ui.scroll_area().classes("col"):
                detail_container = ui.column().classes("w-full gap-1")

    with ui.footer().classes("bg-grey-2 text-grey-9 q-px-md q-py-xs"):
        status_container = ui.row().classes("items-center w-full no-wrap q-gutter-sm")

    _refresh_all()
    show_detail("")  # initial empty-state in the detail pane
