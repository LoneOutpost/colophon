"""Entrypoint: build the app and run the NiceGUI server."""

from __future__ import annotations

import logging
import os
import secrets

from nicegui import ui

from colophon.adapters.config import (
    default_config_path,
    ensure_config_file,
    load_config,
    save_config,
)
from colophon.app_context import AppContext
from colophon.controller import AppController
from colophon.ui import create_app

logger = logging.getLogger(__name__)


def configure_logging(level_name: str | None = None) -> int:
    """Set the log level for application loggers from the process environment.

    Reads ``COLOPHON_LOG_LEVEL`` (default ``INFO``); set it to ``DEBUG`` to see
    the per-book per-phase scan decisions. An unknown value falls back to INFO.
    Returns the numeric level applied.
    """
    name = (level_name or os.environ.get("COLOPHON_LOG_LEVEL") or "INFO").upper()
    level = logging.getLevelNamesMapping().get(name, logging.INFO)
    logging.basicConfig(level=level)
    logging.getLogger("colophon").setLevel(level)
    return level


def main() -> None:
    configure_logging()
    created = ensure_config_file()
    if created:
        logger.info(f"wrote a default config file at {default_config_path()}")
    config = load_config()
    if not config.storage_secret:
        config.storage_secret = secrets.token_hex(32)
        save_config(config, default_config_path())
        logger.info("generated a storage secret for per-tab view persistence")
    ctx = AppContext.create(config)
    create_app(AppController(ctx))
    run_kwargs: dict[str, object] = {}
    if ctx.config.root_path:
        run_kwargs["root_path"] = ctx.config.root_path
    ui.run(
        title="Colophon", reload=False, show=False, port=ctx.config.port,
        storage_secret=ctx.config.storage_secret, **run_kwargs,
    )


if __name__ in {"__main__", "__mp_main__"}:
    main()
