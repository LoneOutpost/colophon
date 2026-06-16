"""Entrypoint: build the app and run the NiceGUI server."""

from __future__ import annotations

from nicegui import ui

from colophon.adapters.config import load_config
from colophon.app_context import AppContext
from colophon.controller import AppController
from colophon.ui import create_app


def main() -> None:
    ctx = AppContext.create(load_config())
    create_app(AppController(ctx))
    ui.run(title="Colophon", reload=False, show=False, port=8080)


if __name__ in {"__main__", "__mp_main__"}:
    main()
