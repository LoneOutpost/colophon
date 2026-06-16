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

from nicegui import ui

from colophon.controller import AppController

logger = logging.getLogger(__name__)

# Height of the carded content area below the app header.
_CONTENT_HEIGHT = "calc(100vh - 96px)"


def _confidence_color(value: float) -> str:
    if value >= 75:
        return "positive"
    if value >= 40:
        return "warning"
    return "negative"


def render_workspace(controller: AppController) -> None:
    selected_ids: set[str] = set()
    scope: dict[str, object] = {"kind": "all", "key": None}

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
            ui.label(book.title or "(untitled)").classes("text-h6")
            with ui.row().classes("items-center q-gutter-xs"):
                ui.badge(f"{book.confidence:.0f}").props(f"color={_confidence_color(book.confidence)}")
                ui.label(book.state.value).classes("text-caption text-grey-7")
            ui.separator().classes("q-my-sm")
            if book.provenance:
                ui.label("Provenance").classes("text-subtitle2")
                with ui.list().props("dense").classes("w-full"):
                    for field, source in book.provenance.items():
                        with ui.item():
                            with ui.item_section():
                                ui.item_label(field)
                            with ui.item_section().props("side"):
                                ui.item_label(source).props("caption")
            ui.button(
                "Mark ready",
                icon="check",
                on_click=lambda b=book: (
                    controller.mark_ready(b),
                    ui.notify("Marked ready"),
                    refresh_list(),
                ),
            ).classes("q-mt-md")

    # --- book list ---
    def refresh_list() -> None:
        list_container.clear()
        books = _books_for_scope()
        with list_container:
            if not books:
                ui.label("No books in this view").classes("text-grey-6 q-pa-md")
                return
            with ui.list().props("separator").classes("w-full"):
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
            with ui.list().classes("w-full"):
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

    def _set_scope(kind: str, key) -> None:
        scope["kind"], scope["key"] = kind, key
        refresh_nav()
        refresh_list()

    def _toggle(book_id: str, on: bool) -> None:
        if on:
            selected_ids.add(book_id)
        else:
            selected_ids.discard(book_id)

    def _refresh_all() -> None:
        refresh_nav()
        refresh_list()

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

    with ui.left_drawer(bordered=True).props("width=260").classes("bg-grey-2"):
        ui.label("Library").classes("text-subtitle2 text-grey-8 q-pa-sm")
        nav_container = ui.column().classes("w-full")

    with ui.row().classes("w-full no-wrap q-gutter-md q-pa-md items-stretch").style(
        f"height: {_CONTENT_HEIGHT}"
    ):
        with ui.card().classes("col-5 column").style("height: 100%"):
            ui.label("Books").classes("text-subtitle1")
            ui.separator()
            with ui.scroll_area().classes("col"):
                list_container = ui.column().classes("w-full")
        with ui.card().classes("col column").style("height: 100%"):
            ui.label("Details").classes("text-subtitle1")
            ui.separator()
            with ui.scroll_area().classes("col"):
                detail_container = ui.column().classes("w-full")

    _refresh_all()
    show_detail("")  # initial empty-state in the detail pane
