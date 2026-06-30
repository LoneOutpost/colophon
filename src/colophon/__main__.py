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
from colophon.core.library_graph import check_file_references
from colophon.ui import create_app

logger = logging.getLogger(__name__)


def configure_logging(level_name: str | None = None) -> int:
    """Set the application log level from the process environment.

    Reads ``COLOPHON_LOG_LEVEL`` (default ``INFO``); set it to ``DEBUG`` to see
    the per-book per-phase scan decisions. An unknown value falls back to INFO.

    Only the ``colophon`` logger tree honors the level: the root stays at INFO so
    ``DEBUG`` does not unleash third-party noise (httpcore, httpx, nicegui). The
    root handler installed by ``basicConfig`` has no level of its own, so it still
    passes the colophon DEBUG records up to the console. Returns the level applied.
    """
    name = (level_name or os.environ.get("COLOPHON_LOG_LEVEL") or "INFO").upper()
    level = logging.getLevelNamesMapping().get(name, logging.INFO)
    logging.basicConfig(level=logging.INFO)
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
    validity = check_file_references(ctx.library_graph)
    if validity.missing_dirs or validity.missing_files:
        logger.warning(
            f"graph: {len(validity.missing_dirs)} directory and "
            f"{len(validity.missing_files)} file references missing on disk"
        )
    else:
        logger.info(f"graph: {len(ctx.library_graph.nodes)} nodes, file references present")
    controller = AppController(ctx)
    healed = controller.rebuild_missing_graph()
    if healed:
        logger.info(f"graph: rebuilt {healed} root(s) from existing books (self-heal)")
    create_app(controller)
    run_kwargs: dict[str, object] = {}
    if ctx.config.root_path:
        run_kwargs["root_path"] = ctx.config.root_path
    ui.run(
        title="Colophon", reload=False, show=False, port=ctx.config.port,
        storage_secret=ctx.config.storage_secret, **run_kwargs,
    )


if __name__ in {"__main__", "__mp_main__"}:
    main()
