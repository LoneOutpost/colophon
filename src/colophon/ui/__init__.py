"""NiceGUI app assembly: register pages bound to an AppController."""

from __future__ import annotations

from fastapi import Response
from nicegui import app, ui

from colophon.controller import AppController
from colophon.ui.acquire import render_acquire
from colophon.ui.settings import render_settings
from colophon.ui.workspace import render_workspace


def create_app(controller: AppController) -> None:
    @app.get("/cover/{book_id}")
    async def cover(book_id: str) -> Response:
        result = await controller.book_cover(book_id)
        if result is None:
            return Response(status_code=404)
        data, mime = result
        return Response(content=data, media_type=mime, headers={"Cache-Control": "public, max-age=3600"})

    @ui.page("/")
    def index() -> None:
        render_workspace(controller)

    @ui.page("/settings")
    def settings() -> None:
        render_settings(controller)

    @ui.page("/acquire")
    def acquire() -> None:
        render_acquire(controller)
