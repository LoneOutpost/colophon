"""NiceGUI app assembly: register pages bound to an AppController."""

from __future__ import annotations

from pathlib import Path

from fastapi import Response
from fastapi.responses import FileResponse
from nicegui import app, ui

from colophon.controller import AppController
from colophon.core.perf import span
from colophon.ui.franchises import render_franchises
from colophon.ui.graph_view import render_graph
from colophon.ui.manage import render_manage
from colophon.ui.settings import render_settings
from colophon.ui.stats import render_stats
from colophon.ui.theme import apply_theme, preload_theme_background, setup_dark_mode
from colophon.ui.workspace import render_workspace

_AUDIO_MIME = {
    ".mp3": "audio/mpeg",
    ".m4b": "audio/mp4",
    ".m4a": "audio/mp4",
    ".aac": "audio/mp4",
    ".ogg": "audio/ogg",
    ".opus": "audio/ogg",
    ".flac": "audio/flac",
}


def _audio_mime(path: Path) -> str:
    """MIME type for an audio file by extension, defaulting to audio/mpeg.
    Explicit because .m4b in particular is commonly mis-guessed by sniffers."""
    return _AUDIO_MIME.get(path.suffix.lower(), "audio/mpeg")


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

    @app.get("/audio/{book_id}/{file_index}")
    def audio(book_id: str, file_index: int) -> Response:
        path = controller.book_audio_path(book_id, file_index)
        if path is None or not path.exists():
            return Response(status_code=404)
        return FileResponse(path, media_type=_audio_mime(path))

    @ui.page("/")
    async def index(filter: str = "", open: str = "") -> None:  # query params: filter, open
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
            render_workspace(controller, dark, initial_filter=filter, open_book_id=open)

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
