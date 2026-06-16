"""Workspace page: three-pane browse + act surface (navigator, list, detail).

UI follows the project polish rules: real icons (no emoji), a loading state on
every async action, no dead controls, and a consistent 2/4 spacing scale.
"""

from __future__ import annotations

import asyncio
import logging

from nicegui import ui

from colophon.controller import AppController

logger = logging.getLogger(__name__)


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

    def show_detail(book_id: str) -> None:
        detail_container.clear()
        book = controller.get_book(book_id)
        if book is None:
            return
        with detail_container:
            ui.label(book.title or "(untitled)").classes("text-xl")
            ui.label(f"Confidence {book.confidence:.0f} · {book.state.value}").classes(
                "text-sm opacity-70"
            )
            for field, source in book.provenance.items():
                ui.label(f"{field}: {source}").classes("text-xs opacity-70")
            ui.button(
                "Mark ready",
                icon="check",
                on_click=lambda b=book: (
                    controller.mark_ready(b),
                    ui.notify("Marked ready"),
                    refresh_list(),
                ),
            )

    def refresh_list() -> None:
        list_container.clear()
        with list_container:
            books = _books_for_scope()
            if not books:
                ui.label("No books in this view").classes("text-sm opacity-60")
            for book in books:
                with ui.row().classes("items-center w-full gap-2"):
                    ui.checkbox(
                        value=book.id in selected_ids,
                        on_change=lambda e, bid=book.id: _toggle(bid, e.value),
                    )
                    ui.badge(f"{book.confidence:.0f}").props(
                        f"color={_confidence_color(book.confidence)}"
                    )
                    ui.label(book.title or "(untitled)").classes("cursor-pointer").on(
                        "click", lambda bid=book.id: show_detail(bid)
                    )

    def refresh_nav() -> None:
        nav_container.clear()
        tree = controller.library_tree()
        with nav_container:
            ui.button("All books", icon="library_books", on_click=lambda: _set_scope("all", None)).props(
                "flat align=left"
            ).classes("w-full")
            if tree.needs_id:
                ui.button(
                    f"Needs identification ({len(tree.needs_id)})",
                    icon="help_outline",
                    on_click=lambda: _set_scope("needs_id", None),
                ).props("flat align=left color=negative").classes("w-full")
            for author in tree.authors:
                ui.button(
                    author.name, icon="person", on_click=lambda name=author.name: _set_scope("author", name)
                ).props("flat align=left").classes("w-full")

    def _set_scope(kind: str, key) -> None:
        scope["kind"], scope["key"] = kind, key
        refresh_list()

    def _toggle(book_id: str, on: bool) -> None:
        if on:
            selected_ids.add(book_id)
        else:
            selected_ids.discard(book_id)

    def _refresh_all() -> None:
        refresh_nav()
        refresh_list()

    # toolbar with per-button loading state (button captured by closure)
    with ui.row().classes("w-full items-center gap-2"):
        scan_btn = ui.button("Scan ingest", icon="search")
        identify_btn = ui.button("Identify pending", icon="travel_explore")
        process_btn = ui.button("Encode + organize", icon="play_arrow")
        ui.space()
        ui.button("Settings", icon="settings", on_click=lambda: ui.navigate.to("/settings")).props("flat")

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
        if await controller.trigger_abs_scan():
            ui.notify("Triggered AudiobookShelf rescan")

    scan_btn.on_click(lambda: _run(scan_btn, _scan, "Scan complete"))
    identify_btn.on_click(lambda: _run(identify_btn, _identify, "Identification complete"))
    process_btn.on_click(lambda: _run(process_btn, _process, "Processing complete"))

    # three panes (containers are created inside their column slots)
    with ui.row().classes("w-full no-wrap gap-4"):
        with ui.column().classes("w-1/5"):
            ui.label("Library").classes("text-sm opacity-70")
            nav_container = ui.column().classes("w-full gap-1")
        with ui.column().classes("w-2/5"):
            list_container = ui.column().classes("w-full gap-1")
        with ui.column().classes("w-2/5"):
            detail_container = ui.column().classes("w-full gap-2")

    _refresh_all()
