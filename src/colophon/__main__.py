"""Entrypoint: build the app and run the NiceGUI server."""

from __future__ import annotations

import logging
import os
import secrets
from pathlib import Path

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
    # Reconcile first: purge graph content left behind by a removed/renamed scan path or a
    # deleted book (orphan book nodes + their dangling edges) before the fill-in self-heal below
    # rebuilds anything. Non-fatal like the other startup heals — a failure degrades to
    # "not reconciled", never blocks startup.
    try:
        purged = controller.reconcile_graph()
        if purged:
            logger.info(f"graph: reconciled away {purged} stale/orphan node(s) at startup")
    except Exception:
        logger.exception("graph reconcile failed; starting with the graph as loaded")
    # The self-heal is an optimization, never a boot dependency: the navigator tolerates
    # books absent from the graph (they show under "Needs identification"). So a failure
    # here (e.g. a graph write conflict from an unusual scan-path config) must degrade to
    # "not healed", never prevent startup.
    try:
        healed = controller.rebuild_missing_graph()
        if healed:
            logger.info(f"graph: rebuilt {healed} root(s) from existing books (self-heal)")
    except Exception:
        logger.exception("graph self-heal failed; starting with the graph as loaded")
    # Backfill local-identification confidence + re-derive state across the catalog so the
    # library opens harmonized with the current graph classifier. Idempotent and non-fatal:
    # a already-harmonized library writes nothing, and a failure must never block startup.
    try:
        updated = controller.recompute_all_identity()
        if updated:
            logger.info(f"identity: backfilled {updated} book(s) from the graph classification")
    except Exception:
        logger.exception("identity backfill failed; starting with stored confidence/state as loaded")
    # Heal covers cached under the old folder-keyed name: clustered books sharing a folder
    # all collided on one file. Clearing the shared cover_path re-fetches each from its own
    # cover_url into a per-book path. Idempotent and non-fatal — never blocks startup.
    try:
        healed_covers = controller.dedupe_colliding_covers()
        if healed_covers:
            logger.info(f"covers: cleared {healed_covers} colliding cover reference(s) to re-fetch")
    except Exception:
        logger.exception("cover dedupe failed; starting with cover references as loaded")
    create_app(controller)
    run_kwargs: dict[str, object] = {}
    if ctx.config.root_path:
        run_kwargs["root_path"] = ctx.config.root_path
    favicon = Path(__file__).parent / "ui" / "assets" / "brand" / "colophon-favicon.svg"
    ui.run(
        title="Colophon", reload=False, show=False, port=ctx.config.port,
        favicon=str(favicon), storage_secret=ctx.config.storage_secret, **run_kwargs,
    )


if __name__ in {"__main__", "__mp_main__"}:
    main()
