"""Dashboard page: collection stats, scan/identify controls, process-ready action."""

from __future__ import annotations

import asyncio
import logging

from nicegui import ui

from colophon.controller import AppController

logger = logging.getLogger(__name__)


def render_dashboard(controller: AppController) -> None:
    stats = controller.dashboard_stats()
    ui.label("Colophon").classes("text-2xl font-bold")
    with ui.row():
        for key in ("total", "needs_review", "ready", "organized"):
            with ui.card():
                ui.label(str(stats.get(key, 0))).classes("text-3xl")
                ui.label(key.replace("_", " "))

    progress_label = ui.label("")
    progress_bar = ui.linear_progress(value=0, show_value=False)
    progress_bar.visible = False

    # Broad `except Exception` at these UI event boundaries is intentional: any
    # failure must surface to the user and be logged rather than crash the handler.
    async def do_scan() -> None:
        try:
            # Run off the event loop: scan is blocking and holds a live sqlite
            # connection (opened with check_same_thread=False, so thread use is safe).
            n = await asyncio.to_thread(controller.scan)
            ui.notify(f"Scanned: {n} book units")
        except Exception:
            logger.exception("scan failed")
            ui.notify("Scan failed (see logs)", type="negative")

    async def do_identify() -> None:
        try:
            await controller.identify_pending()
            ui.notify("Identification complete")
        except Exception:
            logger.exception("identify failed")
            ui.notify("Identify failed (see logs)", type="negative")

    async def do_process() -> None:
        books = controller.ready_books()
        total = len(books)
        if total == 0:
            ui.notify("No books are ready to process")
            return
        progress_bar.visible = True
        progress_bar.value = 0
        organized = 0
        for i, book in enumerate(books, start=1):
            progress_label.text = f"Processing {i} of {total}: {book.title or book.id}"
            try:
                result = await asyncio.to_thread(controller.process_one, book, confirm_delete=False)
                if result.organized:
                    organized += 1
            except Exception:
                logger.exception("process_one failed")
            progress_bar.value = i / total
        progress_label.text = f"Done: {organized} of {total} organized"
        progress_bar.visible = False
        if await controller.trigger_abs_scan():
            ui.notify("Triggered AudiobookShelf rescan")
        ui.notify(f"Processed {total} books; {organized} organized")

    with ui.row():
        ui.button("Scan ingest", on_click=do_scan)
        ui.button("Identify pending", on_click=do_identify)
        ui.button("Encode + organize ready", on_click=do_process)
    ui.link("Triage queue", "/triage")
    ui.link("Settings", "/settings")
