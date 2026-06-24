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
from colophon.core.pathscheme import sample_target
from colophon.core.tokens import TOKENS
from colophon.ui.chrome import page_header

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


def _token_reference() -> None:
    """One help block listing every $Token with its parse/build tag, from core/tokens."""
    def line(tok) -> str:
        if tok.parses and tok.builds:
            tag = "parse + build"
        elif tok.builds:
            tag = "build only"
        else:
            tag = "parse only"
        return f"`${tok.name}` ({tag}) {tok.description}"

    ui.markdown(
        "**Tokens** (shared by the scan and organize patterns):\n\n"
        + "\n".join(f"- {line(t)}" for t in TOKENS)
        + "\n\nUse `$$` for a literal `$`. Unknown tokens and missing values render empty; "
        "`$Skip` matches and discards a run when parsing."
    ).classes("text-caption text-grey")


def render_settings(controller: AppController) -> None:
    cfg = controller.ctx.config

    with page_header(controller, "settings", icon="auto_stories"):
        pass

    field = "outlined dense"
    with ui.column().classes("w-full items-center q-pa-md"):
        with ui.column().classes("w-full gap-4").style("max-width: 760px"):
            with _section(
                "Scanning (read metadata in)",
                "Folders to scan, and how to read fields out of existing filenames and "
                "folder names. These do not write anything.",
            ):
                scan_paths = ui.textarea(
                    "Scan paths (one per line)", value=_paths_to_text(cfg.scan_paths)
                ).props(field).classes("w-full")
                template = ui.input(
                    "Filename template (parses fields out of a filename)",
                    value=cfg.filename_template,
                ).props(field).classes("w-full")
                scheme = ui.input(
                    "Directory scheme (infers fields from folder names, e.g. "
                    "Author/Series/Title; blank disables)",
                    value=cfg.directory_scheme,
                ).props(field).classes("w-full")

            with _section(
                "Organizing (write files out)",
                "Where organized M4Bs are written and how they are named, using "
                "LazyLibrarian-style $Token markup so the layout matches a "
                "LazyLibrarian library.",
            ):
                library_root = ui.input(
                    "Library root (destination for organized M4Bs)",
                    value=str(cfg.library_root or ""),
                ).props(field).classes("w-full")
                folder_pat = ui.input(
                    "Folder pattern", value=cfg.organize_folder_pattern
                ).props(field).classes("w-full")
                file_pat = ui.input(
                    "File name pattern (no extension)", value=cfg.organize_file_pattern
                ).props(field).classes("w-full")

                _token_reference()

                preview = ui.label("").classes("text-caption text-weight-medium")

                def _update_preview() -> None:
                    preview.set_text(
                        "Preview: " + sample_target(folder_pat.value, file_pat.value)
                    )

                folder_pat.on_value_change(lambda _e: _update_preview())
                file_pat.on_value_change(lambda _e: _update_preview())
                _update_preview()

                with ui.row().classes("items-center w-full no-wrap q-gutter-sm"):
                    import_path = ui.input("LazyLibrarian config.ini path").props(
                        field
                    ).classes("col")

                    def _import_ll() -> None:
                        raw = import_path.value.strip()
                        if not raw:
                            ui.notify("Enter a config.ini path first", type="warning")
                            return
                        try:
                            folder, file = controller.import_ll_patterns(Path(raw))
                        except FileNotFoundError:
                            ui.notify(
                                "Couldn't read LazyLibrarian patterns from that path",
                                type="negative",
                            )
                            return
                        folder_pat.set_value(folder)
                        file_pat.set_value(file)
                        _update_preview()
                        ui.notify("Imported patterns from LazyLibrarian config.ini")

                    ui.button("Import", icon="download", on_click=_import_ll).props("flat")

            with _section(
                "Encoding & review",
                "Transcode bitrate for MP3 sources, and the confidence score at or "
                "above which a book is automatically marked ready.",
            ):
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

            with _section(
                "AudiobookShelf",
                "Trigger an AudiobookShelf library rescan after organizing. Server "
                "URL, an API token, and the id of the library to rescan.",
            ):
                abs_url = ui.input("Server URL", value=cfg.audiobookshelf_url or "").props(
                    field
                ).classes("w-full")
                abs_token = ui.input(
                    "API token", value=cfg.audiobookshelf_token or "", password=True
                ).props(f"{field} autocomplete=off").classes("w-full")
                abs_lib = ui.input(
                    "Library id", value=cfg.audiobookshelf_library_id or ""
                ).props(field).classes("w-full")

            with _section(
                "abs-agg",
                "Self-hosted audiobook metadata aggregator. Set its base URL to "
                "auto-discover and enable its providers as match sources.",
            ):
                abs_agg_url = ui.input(
                    "Base URL", value=cfg.abs_agg_url or ""
                ).props(field).classes("w-full")

            with _section(
                "Real-Debrid",
                "Browse and download audiobooks from your Real-Debrid account on the "
                "Acquire page. Private API token, and where downloads land before ingest.",
            ):
                rd_token = ui.input(
                    "API token", value=cfg.real_debrid_token or "", password=True
                ).props(f"{field} autocomplete=off").classes("w-full")
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

            with _section(
                "Genres",
                "Control which genres survive matching: an optional whitelist, the "
                "accepted-genre list, and rename mappings applied on Normalize.",
            ):
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

            # --- Match sources (enable/disable + authority order) ---
            source_rows = [
                {"name": name, "label": label, "enabled": enabled}
                for name, label, enabled in controller.source_settings()
            ]

            def _set_enabled(row: dict, value: bool) -> None:
                row["enabled"] = value

            def _render_sources() -> None:
                sources_box.clear()
                with sources_box:
                    for i, row in enumerate(source_rows):
                        with ui.row().classes("w-full items-center no-wrap q-gutter-sm"):
                            cb = ui.checkbox(value=row["enabled"]).props("dense")
                            cb.on_value_change(
                                lambda e, r=row: _set_enabled(r, e.value)
                            )
                            ui.label(row["label"]).classes("col")
                            ui.button(
                                icon="keyboard_arrow_up",
                                on_click=lambda _e, idx=i: _move(idx, -1),
                            ).props("flat dense round").set_enabled(i > 0)
                            ui.button(
                                icon="keyboard_arrow_down",
                                on_click=lambda _e, idx=i: _move(idx, 1),
                            ).props("flat dense round").set_enabled(
                                i < len(source_rows) - 1
                            )

            def _move(idx: int, delta: int) -> None:
                j = idx + delta
                if 0 <= j < len(source_rows):
                    source_rows[idx], source_rows[j] = source_rows[j], source_rows[idx]
                    _render_sources()

            with _section(
                "Match sources",
                "Enable/disable providers and order them by authority "
                "(top = most trusted).",
            ):
                sources_box = ui.column().classes("w-full gap-1")
                _render_sources()

            def do_save() -> None:
                try:
                    new = Config(
                        db_path=cfg.db_path,  # unchanged here; db path edits need a restart
                        scan_paths=_text_to_paths(scan_paths.value),
                        library_root=_opt_path(library_root.value),
                        organize_folder_pattern=folder_pat.value or "$Author/$Title",
                        organize_file_pattern=file_pat.value or "$Title",
                        filename_template=template.value or "$Author - $Title",
                        directory_scheme=scheme.value,
                        review_threshold=float(threshold.value),
                        transcode_bitrate=bitrate.value or "64k",
                        worker_pool_size=cfg.worker_pool_size,
                        port=int(port.value),
                        root_path=root_path.value.strip(),
                        audiobookshelf_url=_opt_str(abs_url.value),
                        audiobookshelf_token=_opt_str(abs_token.value),
                        audiobookshelf_library_id=_opt_str(abs_lib.value),
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
                        source_order=[r["name"] for r in source_rows],
                        disabled_sources=[
                            r["name"] for r in source_rows if not r["enabled"]
                        ],
                    )
                    controller.save_settings(new)
                    ui.notify("Settings saved")
                except Exception:
                    logger.exception("saving settings failed")
                    ui.notify("Could not save settings (see logs)", type="negative")

            with ui.row().classes("w-full justify-end q-mt-sm"):
                ui.button("Save changes", icon="save", on_click=do_save).props("unelevated")
