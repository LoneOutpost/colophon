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

    form = ui.column().classes("w-full max-w-2xl gap-2")
    with form:
        scan_paths = ui.textarea(
            "Scan paths (one per line)", value=_paths_to_text(cfg.scan_paths)
        ).classes("w-full")
        library_root = ui.input("Library root", value=str(cfg.library_root or "")).classes("w-full")
        ll_ini = ui.input(
            "LazyLibrarian config.ini path", value=str(cfg.lazylibrarian_config_ini or "")
        ).classes("w-full")
        template = ui.input("Filename template", value=cfg.filename_template).classes("w-full")
        threshold = ui.number(
            "Review threshold", value=cfg.review_threshold, min=0, max=100
        ).classes("w-full")
        bitrate = ui.input("Transcode bitrate", value=cfg.transcode_bitrate).classes("w-full")
        port = ui.number("Port", value=cfg.port, min=1, max=65535).classes("w-full")
        root_path = ui.input(
            "Root path (reverse-proxy base, e.g. /colophon)", value=cfg.root_path
        ).classes("w-full")

        ui.label("AudiobookShelf").classes("text-lg mt-4")
        abs_url = ui.input("ABS URL", value=cfg.audiobookshelf_url or "").classes("w-full")
        abs_token = ui.input(
            "ABS token", value=cfg.audiobookshelf_token or "", password=True
        ).classes("w-full")
        abs_lib = ui.input(
            "ABS library id", value=cfg.audiobookshelf_library_id or ""
        ).classes("w-full")

        ui.label("LazyLibrarian").classes("text-lg mt-4")
        ll_url = ui.input("LL URL", value=cfg.lazylibrarian_url or "").classes("w-full")
        ll_key = ui.input(
            "LL API key", value=cfg.lazylibrarian_api_key or "", password=True
        ).classes("w-full")

        ui.label("Hardcover").classes("text-lg mt-4")
        hc_token = ui.input(
            "Hardcover API token", value=cfg.hardcover_api_token or "", password=True
        ).classes("w-full")

        ui.label("Real-Debrid").classes("text-lg mt-4")
        rd_token = ui.input(
            "Real-Debrid token", value=cfg.real_debrid_token or "", password=True
        ).classes("w-full")
        rd_dir = ui.input(
            "Download directory (blank = default)",
            value=str(cfg.real_debrid_download_dir or ""),
        ).classes("w-full")
        rd_status = ui.label("").classes("text-caption text-grey-7")

        async def test_rd() -> None:
            # Use the value currently typed in the field for the test.
            controller.ctx.config.real_debrid_token = _opt_str(rd_token.value)
            if not controller.rd_configured():
                rd_status.set_text("Enter a token first")
                return
            rd_status.set_text("Testing...")
            try:
                user = await controller.rd_test_connection()
                rd_status.set_text(f"Connected as {user.username}")
            except Exception as e:  # surface any failure to the operator (BLE001 intentional)
                logger.warning(f"RD test connection failed: {e}")
                rd_status.set_text("Connection failed (check the token)")

        ui.button("Test connection", icon="wifi_tethering", on_click=test_rd).props("flat")

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
                port=int(port.value),
                root_path=root_path.value.strip(),
                audiobookshelf_url=_opt_str(abs_url.value),
                audiobookshelf_token=_opt_str(abs_token.value),
                audiobookshelf_library_id=_opt_str(abs_lib.value),
                lazylibrarian_url=_opt_str(ll_url.value),
                lazylibrarian_api_key=_opt_str(ll_key.value),
                hardcover_api_token=_opt_str(hc_token.value),
                real_debrid_token=_opt_str(rd_token.value),
                real_debrid_download_dir=_opt_path(rd_dir.value),
            )
            controller.save_settings(new)
            ui.notify("Settings saved")
        except Exception:
            logger.exception("saving settings failed")
            ui.notify("Could not save settings (see logs)", type="negative")

    ui.button("Save", on_click=do_save).classes("mt-4")
    ui.link("Dashboard", "/")
