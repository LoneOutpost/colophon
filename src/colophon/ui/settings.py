"""Settings page: edit and persist the active configuration."""

from __future__ import annotations

import logging
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

from nicegui import ui

from colophon.adapters.config import Config
from colophon.controller import AppController
from colophon.core.normalize import NORMALIZABLE_FIELDS
from colophon.ui.tabs import app_tabs
from colophon.ui.theme import apply_theme, dark_mode_button, setup_dark_mode

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


@contextmanager
def _section(title: str, subtitle: str | None = None) -> Iterator[None]:
    """A titled settings card; fields added in the body stack inside it."""
    with ui.card().classes("w-full q-pa-md"):
        ui.label(title).classes("text-subtitle1 text-weight-medium")
        if subtitle:
            ui.label(subtitle).classes("text-caption text-grey q-mb-xs")
        with ui.column().classes("w-full gap-3 q-mt-sm"):
            yield


def render_settings(controller: AppController) -> None:
    apply_theme()
    dark = setup_dark_mode()
    cfg = controller.ctx.config

    with ui.header(elevated=True).classes("items-center q-px-md"):
        ui.icon("auto_stories", color="primary").classes("text-h5")
        ui.label("Colophon").classes("text-h6 q-ml-sm text-weight-medium")
        app_tabs(controller, "settings")
        ui.space()
        dark_mode_button(dark)

    field = "outlined dense"
    with ui.column().classes("w-full items-center q-pa-md"):
        with ui.column().classes("w-full gap-4").style("max-width: 760px"):
            with _section("Library", "Where books are scanned from and organized to."):
                scan_paths = ui.textarea(
                    "Scan paths (one per line)", value=_paths_to_text(cfg.scan_paths)
                ).props(field).classes("w-full")
                library_root = ui.input(
                    "Library root", value=str(cfg.library_root or "")
                ).props(field).classes("w-full")
                template = ui.input(
                    "Filename template", value=cfg.filename_template
                ).props(field).classes("w-full")

            with _section("Encoding & review"):
                bitrate = ui.input(
                    "Transcode bitrate", value=cfg.transcode_bitrate
                ).props(field).classes("w-full")
                threshold = ui.number(
                    "Review threshold", value=cfg.review_threshold, min=0, max=100
                ).props(field).classes("w-full")

            with _section("Server", "Changes take effect on the next restart."):
                port = ui.number("Port", value=cfg.port, min=1, max=65535).props(field).classes(
                    "w-full"
                )
                root_path = ui.input(
                    "Root path (reverse-proxy base, e.g. /colophon)", value=cfg.root_path
                ).props(field).classes("w-full")

            with _section("AudiobookShelf", "Trigger a library rescan after organizing."):
                abs_url = ui.input("Server URL", value=cfg.audiobookshelf_url or "").props(
                    field
                ).classes("w-full")
                abs_token = ui.input(
                    "API token", value=cfg.audiobookshelf_token or "", password=True
                ).props(field).classes("w-full")
                abs_lib = ui.input(
                    "Library id", value=cfg.audiobookshelf_library_id or ""
                ).props(field).classes("w-full")

            with _section("LazyLibrarian", "Read-only status lookups and path patterns."):
                ll_ini = ui.input(
                    "config.ini path", value=str(cfg.lazylibrarian_config_ini or "")
                ).props(field).classes("w-full")
                ll_url = ui.input("Server URL", value=cfg.lazylibrarian_url or "").props(
                    field
                ).classes("w-full")
                ll_key = ui.input(
                    "API key", value=cfg.lazylibrarian_api_key or "", password=True
                ).props(field).classes("w-full")

            with _section("abs-agg", "Audiobook metadata aggregator. Set its base URL to enable its providers."):
                abs_agg_url = ui.input(
                    "Base URL", value=cfg.abs_agg_url or ""
                ).props(field).classes("w-full")

            with _section("Real-Debrid", "Browse and download audiobooks from your account."):
                rd_token = ui.input(
                    "API token", value=cfg.real_debrid_token or "", password=True
                ).props(field).classes("w-full")
                rd_dir = ui.input(
                    "Download directory (blank = default)",
                    value=str(cfg.real_debrid_download_dir or ""),
                ).props(field).classes("w-full")
                with ui.row().classes("items-center w-full no-wrap q-gutter-sm"):
                    rd_status = ui.label("").classes("text-caption text-grey")

                    async def test_rd() -> None:
                        # Test the typed value without persisting it.
                        token = _opt_str(rd_token.value)
                        if not token:
                            rd_status.set_text("Enter a token first")
                            return
                        rd_status.set_text("Testing...")
                        try:
                            user = await controller.rd_test_connection(token)
                            rd_status.set_text(f"Connected as {user.username}")
                        except Exception as e:  # surface failure to the operator (BLE001 intentional)
                            logger.warning(f"RD test connection failed: {e}")
                            rd_status.set_text("Connection failed (check the token)")

                    ui.button(
                        "Test connection", icon="wifi_tethering", on_click=test_rd
                    ).props("flat").classes("q-ml-auto")

            with _section("Genres", "Canonicalize and optionally restrict genres."):
                genre_whitelist = ui.switch(
                    "Enforce accepted-genres whitelist", value=cfg.genre_whitelist_enabled
                )
                accepted = ui.select(
                    sorted(cfg.accepted_genres), value=list(cfg.accepted_genres),
                    multiple=True, new_value_mode="add-unique", label="Accepted genres",
                ).props("use-chips use-input dense").classes("w-full")
                ui.label("Synonym mapping (from → to)").classes("text-caption text-grey q-mt-sm")
                mapping_rows: list[tuple] = []
                mapping_box = ui.column().classes("w-full")

                def _add_mapping_row(frm: str = "", to: str = "") -> None:
                    with mapping_box, ui.row().classes("items-center w-full no-wrap q-gutter-sm") as row:
                        frm_in = ui.input("from", value=frm).props("dense").classes("col")
                        to_in = ui.input("to", value=to).props("dense").classes("col")
                        entry = (frm_in, to_in, row)
                        mapping_rows.append(entry)
                        ui.button(
                            icon="close",
                            on_click=lambda e=entry: (e[2].delete(), mapping_rows.remove(e)),
                        ).props("flat dense round")

                for _frm, _to in sorted(cfg.genre_mapping.items()):
                    _add_mapping_row(_frm, _to)
                ui.button("Add row", icon="add", on_click=lambda: _add_mapping_row()).props("flat dense")

            with _section("Matching", "Fields to auto-normalize when a match is applied."):
                normalize_on_match = ui.select(
                    NORMALIZABLE_FIELDS,
                    value=list(cfg.normalize_on_match),
                    multiple=True,
                    label="Auto-normalize on match",
                ).props("use-chips dense").classes("w-full")

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
                        abs_agg_url=_opt_str(abs_agg_url.value),
                        real_debrid_token=_opt_str(rd_token.value),
                        real_debrid_download_dir=_opt_path(rd_dir.value),
                        genre_whitelist_enabled=bool(genre_whitelist.value),
                        accepted_genres=[g for g in (accepted.value or []) if g and g.strip()],
                        genre_mapping={
                            f.value.strip(): t.value.strip()
                            for f, t, _row in mapping_rows
                            if f.value and f.value.strip() and t.value and t.value.strip()
                        },
                        normalize_on_match=[
                            f for f in (normalize_on_match.value or []) if f
                        ],
                    )
                    controller.save_settings(new)
                    ui.notify("Settings saved")
                except Exception:
                    logger.exception("saving settings failed")
                    ui.notify("Could not save settings (see logs)", type="negative")

            with ui.row().classes("w-full justify-end q-mt-sm"):
                ui.button("Save changes", icon="save", on_click=do_save).props("unelevated")
