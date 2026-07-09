"""NiceGUI app assembly: register pages bound to an AppController."""

from __future__ import annotations

from pathlib import Path

from fastapi import Response
from nicegui import app, ui

from colophon.controller import AppController
from colophon.core.perf import span
from colophon.ui.acquire import render_acquire
from colophon.ui.franchises import render_franchises
from colophon.ui.graph_view import render_graph
from colophon.ui.manage import render_manage
from colophon.ui.settings import render_settings
from colophon.ui.stats import render_stats
from colophon.ui.theme import apply_theme, preload_theme_background, setup_dark_mode
from colophon.ui.workspace import render_workspace


def create_app(controller: AppController) -> None:
    # Serve bundled static assets (self-hosted fonts, etc.) so the UI works offline.
    app.add_static_files("/assets", str(Path(__file__).parent / "assets"))

    @app.get("/cover/{book_id}")
    async def cover(book_id: str, size: str = "") -> Response:
        result = await controller.book_cover(book_id, thumb=(size == "thumb"))
        if result is None:
            return Response(status_code=404)
        data, mime = result
        return Response(content=data, media_type=mime, headers={"Cache-Control": "public, max-age=3600"})

    @ui.page("/")
    async def index(filter: str = "") -> None:  # the URL query-param name is "filter"
        # Apply the full theme (palette, base CSS, dark-mode class) in this synchronous
        # prefix so it ships in the initial HTML, before we await the client. The
        # workspace itself renders only after connect; without the theme up front it
        # would briefly paint the light (warm) surfaces until dark-mode lands — a flash
        # on every visit. Sync pages avoid this because their content ships up front too.
        preload_theme_background()
        apply_theme()
        dark = setup_dark_mode()
        await ui.context.client.connected()
        with span("render / workspace"):
            render_workspace(controller, dark, initial_filter=filter)

    @ui.page("/manage")
    def manage(kind: str | None = None, filter: str = "") -> None:  # the URL query-param name is "filter"
        preload_theme_background()
        with span("render /manage"):
            render_manage(controller, initial_kind=kind, initial_filter=filter)

    @ui.page("/stats")
    def stats() -> None:
        preload_theme_background()
        render_stats(controller)

    @ui.page("/franchises")
    def franchises() -> None:
        preload_theme_background()
        render_franchises(controller)

    @ui.page("/graph")
    async def graph(mode: str = "explorer", focal: str | None = None,
                    hide: str | None = None, depth: str | None = None) -> None:
        preload_theme_background()
        await ui.context.client.connected()
        render_graph(controller, mode=mode, focal=focal, hide=hide, depth=depth)

    @ui.page("/settings")
    def settings() -> None:
        preload_theme_background()
        render_settings(controller)

    @ui.page("/acquire")
    def acquire(book: str = "") -> None:  # the URL query-param name is "book"
        preload_theme_background()
        render_acquire(controller, book_id=book)
