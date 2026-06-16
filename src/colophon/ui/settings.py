"""Settings page: edit and persist the active configuration."""

from __future__ import annotations

import logging
from pathlib import Path

from nicegui import ui

from colophon.adapters.config import Config
from colophon.controller import AppController

logger = logging.getLogger(__name__)


def _paths_to_text(paths: list[Path]) -> str:
    return "\n".join(str(p) for p in paths)


def _text_to_paths(text: str) -> list[Path]:
    return [Path(line.strip()) for line in text.splitlines() if line.strip()]


def _opt_path(value: str) -> Path | None:
    value = value.strip()
    return Path(value) if value else None


def _opt_str(value: str) -> str | None:
    value = value.strip()
    return value or None


def render_settings(controller: AppController) -> None:
    cfg = controller.ctx.config
    ui.label("Settings").classes("text-2xl font-bold")

    scan_paths = ui.textarea("Scan paths (one per line)", value=_paths_to_text(cfg.scan_paths))
    library_root = ui.input("Library root", value=str(cfg.library_root or ""))
    ll_ini = ui.input("LazyLibrarian config.ini path", value=str(cfg.lazylibrarian_config_ini or ""))
    template = ui.input("Filename template", value=cfg.filename_template)
    threshold = ui.number("Review threshold", value=cfg.review_threshold, min=0, max=100)
    bitrate = ui.input("Transcode bitrate", value=cfg.transcode_bitrate)

    ui.label("AudiobookShelf").classes("text-lg mt-4")
    abs_url = ui.input("ABS URL", value=cfg.audiobookshelf_url or "")
    abs_token = ui.input("ABS token", value=cfg.audiobookshelf_token or "", password=True)
    abs_lib = ui.input("ABS library id", value=cfg.audiobookshelf_library_id or "")

    ui.label("LazyLibrarian").classes("text-lg mt-4")
    ll_url = ui.input("LL URL", value=cfg.lazylibrarian_url or "")
    ll_key = ui.input("LL API key", value=cfg.lazylibrarian_api_key or "", password=True)

    ui.label("Hardcover").classes("text-lg mt-4")
    hc_token = ui.input("Hardcover API token", value=cfg.hardcover_api_token or "", password=True)

    def do_save() -> None:
        try:
            new = Config(
                db_path=cfg.db_path,  # unchanged here; db path edits need a restart
                scan_paths=_text_to_paths(scan_paths.value),
                library_root=_opt_path(library_root.value),
                lazylibrarian_config_ini=_opt_path(ll_ini.value),
                filename_template=template.value or "%author% - %title%",
                review_threshold=float(threshold.value),
                transcode_bitrate=bitrate.value or "64k",
                worker_pool_size=cfg.worker_pool_size,
                audiobookshelf_url=_opt_str(abs_url.value),
                audiobookshelf_token=_opt_str(abs_token.value),
                audiobookshelf_library_id=_opt_str(abs_lib.value),
                lazylibrarian_url=_opt_str(ll_url.value),
                lazylibrarian_api_key=_opt_str(ll_key.value),
                hardcover_api_token=_opt_str(hc_token.value),
            )
            controller.save_settings(new)
            ui.notify("Settings saved")
        except Exception:
            logger.exception("saving settings failed")
            ui.notify("Could not save settings (see logs)", type="negative")

    ui.button("Save", on_click=do_save).classes("mt-4")
    ui.link("Dashboard", "/")
