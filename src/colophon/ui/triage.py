"""Triage page: grouped book queue (master) with a detail panel."""

from __future__ import annotations

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


def render_triage(controller: AppController) -> None:
    ui.label("Triage").classes("text-2xl font-bold")
    detail = ui.column()

    def show_detail(book_id: str) -> None:
        detail.clear()
        book = controller.get_book(book_id)
        if book is None:
            return
        with detail:
            ui.label(book.title or "(untitled)").classes("text-xl")
            ui.label(f"confidence {book.confidence:.0f} · {book.state.value}")
            for field, source in book.provenance.items():
                ui.label(f"{field}: {source}").classes("text-xs")
            ui.button("Mark ready", on_click=lambda b=book: (controller.mark_ready(b), ui.notify("marked ready")))

            async def compare_matches(b=book) -> None:
                with ui.dialog() as dialog, ui.card():
                    ui.label(f"Matches for {b.title or '(untitled)'}").classes("text-lg")
                    container = ui.column()
                    with container:
                        ui.label("Searching sources…")
                    try:
                        matches = await controller.get_matches(b)
                    except Exception:
                        logger.exception("get_matches failed")
                        matches = []
                    container.clear()
                    with container:
                        if not matches:
                            ui.label("No matches found")
                        for m in matches[:10]:
                            with ui.row().classes("items-center"):
                                authors = ", ".join(m.authors) or "?"
                                year = f" ({m.publish_year})" if m.publish_year else ""
                                ui.label(f"[{m.provider}] {m.title or '?'} — {authors}{year}")

                                def _apply(result=m) -> None:
                                    controller.apply_match(b, result)
                                    dialog.close()
                                    ui.notify(f"Applied {result.provider} match")
                                    show_detail(b.id)

                                ui.button("Apply", on_click=_apply)
                    ui.button("Close", on_click=dialog.close)
                dialog.open()

            ui.button("Compare matches", on_click=compare_matches)

    with ui.row().classes("w-full no-wrap gap-4"):
        with ui.column().classes("w-1/2"):
            for group in controller.triage_groups():
                with ui.expansion(f"{group.label} ({len(group.books)})", value=True):
                    for book in group.books:
                        with ui.row().classes("items-center cursor-pointer").on(
                            "click", lambda bid=book.id: show_detail(bid)
                        ):
                            ui.badge(f"{book.confidence:.0f}", color=_confidence_color(book.confidence))
                            ui.label(book.title or "(untitled)")
        with ui.column().classes("w-1/2") as detail_column:
            detail.move(detail_column)
    ui.link("Dashboard", "/")
