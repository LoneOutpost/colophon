"""NiceGUI app assembly: register pages bound to an AppController."""

from __future__ import annotations

from pathlib import Path

from fastapi import Response
from nicegui import app, ui

from colophon.controller import AppController
from colophon.ui.acquire import render_acquire
from colophon.ui.manage import render_manage
from colophon.ui.settings import render_settings
from colophon.ui.workspace import render_workspace


def create_app(controller: AppController) -> None:
    # Serve bundled static assets (self-hosted fonts, etc.) so the UI works offline.
    app.add_static_files("/assets", str(Path(__file__).parent / "assets"))

    @app.get("/cover/{book_id}")
    async def cover(book_id: str) -> Response:
        result = await controller.book_cover(book_id)
        if result is None:
            return Response(status_code=404)
        data, mime = result
        return Response(content=data, media_type=mime, headers={"Cache-Control": "public, max-age=3600"})

    @ui.page("/")
    async def index(filter: str = "") -> None:  # the URL query-param name is "filter"
        await ui.context.client.connected()
        render_workspace(controller, initial_filter=filter)

    @ui.page("/manage")
    def manage() -> None:
        render_manage(controller)

    @ui.page("/settings")
    def settings() -> None:
        render_settings(controller)

    @ui.page("/acquire")
    def acquire() -> None:
        render_acquire(controller)
