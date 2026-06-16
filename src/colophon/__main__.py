"""Entrypoint: build the app and run the NiceGUI server."""

from __future__ import annotations

import logging

from nicegui import ui

from colophon.adapters.config import default_config_path, ensure_config_file, load_config
from colophon.app_context import AppContext
from colophon.controller import AppController
from colophon.ui import create_app

logger = logging.getLogger(__name__)


def main() -> None:
    created = ensure_config_file()
    if created:
        logger.info(f"wrote a default config file at {default_config_path()}")
    ctx = AppContext.create(load_config())
    create_app(AppController(ctx))
    run_kwargs: dict[str, object] = {}
    if ctx.config.root_path:
        run_kwargs["root_path"] = ctx.config.root_path
    ui.run(title="Colophon", reload=False, show=False, port=ctx.config.port, **run_kwargs)


if __name__ in {"__main__", "__mp_main__"}:
    main()
