"""NiceGUI app assembly: register pages bound to an AppController."""

from __future__ import annotations

from pathlib import Path

from fastapi import Response
from nicegui import app, ui

from colophon.controller import AppController
from colophon.ui.acquire import render_acquire
from colophon.ui.graph_view import render_graph
from colophon.ui.manage import render_manage
from colophon.ui.settings import render_settings
from colophon.ui.stats import render_stats
from colophon.ui.theme import preload_theme_background
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
        # Paint the themed background into the initial HTML before awaiting the client,
        # so this page (the most navigated to) doesn't flash light on every visit.
        preload_theme_background()
        await ui.context.client.connected()
        render_workspace(controller, initial_filter=filter)

    @ui.page("/manage")
    def manage(kind: str | None = None, filter: str = "") -> None:  # the URL query-param name is "filter"
        preload_theme_background()
        render_manage(controller, initial_kind=kind, initial_filter=filter)

    @ui.page("/stats")
    def stats() -> None:
        preload_theme_background()
        render_stats(controller)

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
    def acquire() -> None:
        preload_theme_background()
        render_acquire(controller)
