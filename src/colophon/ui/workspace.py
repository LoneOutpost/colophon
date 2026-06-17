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
    view: dict[str, object] = {"mode": "library", "cwd": None}

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
        books = list(tree.needs_id)
        for a in tree.authors:
            books += [b for s in a.series for b in s.books] + a.standalone
        return books

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
                    body.clear()
                    with body:
                        if not matches:
                            ui.label("No matches found").classes("text-grey-6")
                        for m in matches[:10]:
                            authors = ", ".join(m.authors) or "unknown"
                            year = f" ({m.publish_year})" if m.publish_year else ""
                            with ui.item(
                                on_click=lambda result=m: (
                                    controller.apply_match(b, result),
                                    dialog.close(),
                                    ui.notify(f"Applied {result.provider}"),
                                    refresh_list(),
                                    show_detail(b.id),
                                )
                            ).props("clickable"):
                                with ui.item_section():
                                    ui.item_label(m.title or "?")
                                    ui.item_label(f"{m.provider} · {authors}{year}").props("caption")
                    ui.button("Close", on_click=dialog.close).props("flat")
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

            with ui.row().classes("q-gutter-sm q-mt-sm"):
                ui.button("Save", icon="save", on_click=_save)
                ui.button("Compare matches", icon="search", on_click=_compare).props("outline")
                ui.button("Write tags", icon="sell", on_click=lambda b=book: _tag_dialog(b)).props("outline")
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
                                ui.item_label(f"{sf.duration_seconds / 60:.0f} min").props("caption")
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

            with ui.row().classes("q-gutter-sm q-mt-sm"):
                ui.button("Apply to selection", icon="done_all", on_click=_apply_bulk)
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
    def refresh_list() -> None:
        list_container.clear()
        books = _books_for_scope()
        with list_container:
            if not books:
                ui.label("No books in this view").classes("text-grey-6 q-pa-md")
                return
            with ui.list().props("separator dense").classes("w-full"):
                for book in books:
                    with ui.item(on_click=lambda bid=book.id: show_detail(bid)).props("clickable"):
                        with ui.item_section().props("avatar"):
                            ui.checkbox(
                                value=book.id in selected_ids,
                                on_change=lambda e, bid=book.id: _toggle(bid, e.value),
                            )
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

    async def _foster_selected_now(button) -> None:
        paths = sorted(foster_selected)
        if not paths:
            ui.notify("No files selected")
            return
        # foster_files does disk renames + a subtree rescan (slow over network
        # mounts), so run it off the event loop and show a busy state.
        button.props("loading=true")
        try:
            results = await asyncio.to_thread(controller.foster_files, paths)
        finally:
            button.props(remove="loading")
        ok = sum(1 for r in results if r.ok)
        failed = len(results) - ok
        foster_selected.clear()
        msg = f"Fostered {ok} file(s)" + (f", {failed} failed" if failed else "")
        ui.notify(msg, type="negative" if failed and not ok else "positive")
        # Stay in Folders mode: redraw the directory browser (moved files now show
        # as subfolders) and refresh the nav so the new books register in Library.
        refresh_nav()
        _render_middle()

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
            root_strs = {str(r) for r in roots}
            if len(roots) > 1:
                ui.select(
                    {str(r): (r.name or str(r)) for r in roots},
                    value=str(cwd) if str(cwd) in root_strs else str(roots[0]),
                    on_change=lambda e: (view.__setitem__("cwd", Path(e.value)), refresh_folders()),
                ).props("dense outlined").classes("w-full q-mb-sm")

            with ui.row().classes("items-center w-full no-wrap q-gutter-xs q-mb-xs"):
                ui.icon("folder_open").classes("text-grey-7")
                ui.label(str(cwd)).classes("text-caption text-grey-7 ellipsis col")
                foster_btn = ui.button(
                    "Foster selected", icon="subdirectory_arrow_right"
                ).props("dense color=primary")
                foster_btn.on_click(lambda b=foster_btn: _foster_selected_now(b))

            listing = controller.list_directory(cwd)
            with ui.list().props("dense bordered").classes("w-full"):
                # "Up" entry: hidden once cwd is a configured scan root, so normal
                # browsing stops at the root (nested roots are not specially handled).
                if cwd not in {Path(str(r)) for r in roots}:
                    with ui.item(
                        on_click=lambda p=cwd.parent: (view.__setitem__("cwd", p), refresh_folders())
                    ).props("clickable"):
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
                        with ui.item(
                            on_click=lambda p=entry.path: (view.__setitem__("cwd", p), refresh_folders())
                        ).props("clickable"):
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
    def _nav_item(label: str, icon: str, active: bool, on_click, color: str | None = None) -> None:
        with ui.item(on_click=on_click).props("clickable" + (" active" if active else "")):
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
            with ui.list().props("dense").classes("w-full"):
                _nav_item("All books", "library_books", kind == "all", lambda: _set_scope("all", None))
                if tree.needs_id:
                    _nav_item(
                        f"Needs identification ({len(tree.needs_id)})",
                        "help_outline",
                        kind == "needs_id",
                        lambda: _set_scope("needs_id", None),
                        color="negative",
                    )
                for author in tree.authors:
                    _nav_item(
                        author.name,
                        "person",
                        kind == "author" and key == author.name,
                        lambda name=author.name: _set_scope("author", name),
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

    def _set_scope(kind: str, key) -> None:
        scope["kind"], scope["key"] = kind, key
        refresh_nav()
        _render_middle()

    def _after_select() -> None:
        n = len(selected_ids)
        if n >= 2:
            show_bulk()
        elif n == 1:
            show_detail(next(iter(selected_ids)))
        else:
            show_detail("")

    def _toggle(book_id: str, on: bool) -> None:
        if on:
            selected_ids.add(book_id)
        else:
            selected_ids.discard(book_id)
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
