"""Settings page: edit and persist the active configuration."""

from __future__ import annotations

import logging
from pathlib import Path

from nicegui import ui

from colophon.controller import AppController
from colophon.core.normalize import NORMALIZABLE_FIELDS
from colophon.core.pathscheme import sample_target
from colophon.core.tokens import TOKENS
from colophon.ui.chrome import page_body, page_header, page_section

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
        + "\n".join(f"- {line(t)}" for t in TOKENS if not t.hidden)
        + "\n\nUse `$$` for a literal `$`. Unknown tokens and missing values render empty; "
        "`$Skip` matches and discards a run when parsing."
        "\n\nIn organize patterns, wrap optional text in `[ ... ]` so it appears only when its "
        "token has a value: `[$SerNum - ]$Title` renders `1 - The Way of Kings` with a series "
        "number and just `The Way of Kings` without one. A group drops if any token inside is "
        "empty; use `[[` and `]]` for literal brackets. Conditional groups are not valid in the "
        "scan patterns above."
    ).classes("text-caption text-grey")


def render_settings(controller: AppController) -> None:
    cfg = controller.ctx.config

    with page_header(controller, "settings"):
        pass

    field = "outlined dense"
    with page_body("read"):
        with page_section(
            "Scanning (read metadata in)",
            "Defaults for reading fields out of filenames and folder names. You can "
            "override these per run in the Scan dialog. These do not write anything.",
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
                "$Author/$Series/$Title; blank disables)",
                value=cfg.directory_scheme,
            ).props(field).classes("w-full")

        with page_section(
            "Organizing (write files out)",
            "Defaults for where organized M4Bs are written and how they are named, "
            "using LazyLibrarian-style $Token markup. You can override these per run "
            "in the Encode + organize dialog.",
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

            ui.label(
                "Series formatting — $Series expands the Series pattern, which composes "
                "$FmtName (Series name pattern) and $FmtNum (Series number pattern). "
                "Use $SerName / $SerNum for the raw name and number."
            ).classes("text-caption colophon-muted q-mt-sm")
            series_pat = ui.input(
                "Series pattern ($Series)", value=cfg.series_pattern
            ).props(field).classes("w-full")
            series_name_pat = ui.input(
                "Series name pattern ($FmtName)", value=cfg.series_name_pattern
            ).props(field).classes("w-full")
            series_number_pat = ui.input(
                "Series number pattern ($FmtNum)", value=cfg.series_number_pattern
            ).props(field).classes("w-full")

            _token_reference()

            preview = ui.label("").classes("text-caption text-weight-medium")

            def _update_preview() -> None:
                preview.set_text(
                    "Preview: " + sample_target(
                        folder_pat.value, file_pat.value,
                        series=series_pat.value,
                        series_name=series_name_pat.value,
                        series_number=series_number_pat.value,
                    )
                )

            for _pat in (folder_pat, file_pat, series_pat, series_name_pat, series_number_pat):
                _pat.on_value_change(lambda _e: _update_preview())
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
                        folder = controller.import_ll_patterns(Path(raw))
                    except FileNotFoundError:
                        ui.notify(
                            "Couldn't read a LazyLibrarian folder pattern from that path",
                            type="negative",
                        )
                        return
                    folder_pat.set_value(folder)
                    _update_preview()
                    ui.notify("Imported folder pattern from LazyLibrarian config.ini")

                ui.button(
                    "Import folder pattern", icon="download", on_click=_import_ll
                ).props("flat")

            reorg_delete = ui.switch(
                "Delete source files after a no-encode reorg (originals are copied,"
                " then removed only once every file is verified)",
                value=cfg.reorg_delete_sources,
            )

            ui.markdown(
                "**Multi-part books** are named once per part using `$Part` and "
                "`$Total`. Wrap them in a conditional group so single-file books are "
                "unaffected, e.g. `$Title[ - Part $Part of $Total]`. If you omit "
                "`$Part`, Colophon appends ` ($Part of $Total)` automatically so parts "
                "never collide. `$Part` is padded to `$Total`'s width; both are empty "
                "for single-file books."
            ).classes("text-caption")

        with page_section(
            "Pattern history",
            "Recently used patterns, offered as quick picks in the Scan, Parse, and "
            "Encode dialogs. Remove any you no longer want, or clear them all.",
        ):
            history_box = ui.column().classes("w-full gap-3")

            def _render_history() -> None:
                c = controller.ctx.config
                history_box.clear()
                with history_box:
                    _history_group(
                        "Filename templates",
                        c.recent_filename_templates,
                        lambda p: p,
                        lambda p: _remove_history(controller.remove_filename_template, p),
                    )
                    _history_group(
                        "Directory schemes",
                        c.recent_directory_schemes,
                        lambda p: p,
                        lambda p: _remove_history(controller.remove_directory_scheme, p),
                    )
                    _history_group(
                        "Organize patterns",
                        c.recent_organize_patterns,
                        lambda op: f"{op.folder} · {op.file}",
                        lambda op: _remove_history(
                            controller.remove_organize_pattern, op.folder, op.file
                        ),
                    )
                    if (
                        c.recent_filename_templates
                        or c.recent_directory_schemes
                        or c.recent_organize_patterns
                    ):
                        ui.button(
                            "Clear all history",
                            icon="delete_sweep",
                            on_click=_clear_history,
                        ).props("flat dense")

            def _history_group(title, items, label, on_remove) -> None:
                ui.label(title).classes("text-caption colophon-muted")
                with ui.row().classes("items-center w-full q-gutter-xs"):
                    if not items:
                        ui.label("None yet").classes("text-caption colophon-muted")
                    for it in items:
                        ui.chip(label(it), removable=True).props("dense outline").classes(
                            "colophon-chip"
                        ).on("remove", lambda _e, i=it: on_remove(i))

            def _remove_history(remover, *parts) -> None:
                remover(*parts)
                _render_history()

            def _clear_history() -> None:
                controller.clear_pattern_history()
                _render_history()
                ui.notify("Cleared pattern history")

            _render_history()

        with page_section(
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

        with page_section("Server", "Changes take effect on the next restart."):
            port = ui.number("Port", value=cfg.port, min=1, max=65535).props(field).classes(
                "w-full"
            )
            root_path = ui.input(
                "Root path (reverse-proxy base, e.g. /colophon)", value=cfg.root_path
            ).props(field).classes("w-full")

        with page_section(
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

        with page_section(
            "abs-agg",
            "Self-hosted audiobook metadata aggregator. Set its base URL to "
            "auto-discover and enable its providers as match sources.",
        ):
            abs_agg_url = ui.input(
                "Base URL", value=cfg.abs_agg_url or ""
            ).props(field).classes("w-full")

        with page_section(
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

        with page_section(
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

        with page_section("Matching", "Fields to auto-normalize when a match is applied."):
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

        with page_section(
            "Match sources",
            "Enable/disable providers and order them by authority "
            "(top = most trusted).",
        ):
            sources_box = ui.column().classes("w-full gap-1")
            _render_sources()

        def do_save() -> None:
            try:
                # Copy from the live config so fields this form doesn't edit
                # (storage_secret, recent_*_patterns, downloads_scan_prompt_seen,
                # db_path, worker_pool_size, ...) are preserved, not reset to defaults.
                new = cfg.model_copy(update={
                    "scan_paths": _text_to_paths(scan_paths.value),
                    "library_root": _opt_path(library_root.value),
                    "organize_folder_pattern": folder_pat.value or "$Author/$Title",
                    "organize_file_pattern": file_pat.value or "$Title",
                    "series_pattern": series_pat.value or "($FmtName $FmtNum)",
                    "series_name_pattern": series_name_pat.value or "$SerName",
                    "series_number_pattern": series_number_pat.value or "Book #$SerNum",
                    "reorg_delete_sources": reorg_delete.value,
                    "filename_template": template.value or "$Author - $Title",
                    "directory_scheme": scheme.value,
                    "review_threshold": float(threshold.value),
                    "transcode_bitrate": bitrate.value or "64k",
                    "port": int(port.value),
                    "root_path": root_path.value.strip(),
                    "audiobookshelf_url": _opt_str(abs_url.value),
                    "audiobookshelf_token": _opt_str(abs_token.value),
                    "audiobookshelf_library_id": _opt_str(abs_lib.value),
                    "abs_agg_url": _opt_str(abs_agg_url.value),
                    "real_debrid_token": _opt_str(rd_token.value),
                    "real_debrid_download_dir": _opt_path(rd_dir.value),
                    "genre_whitelist_enabled": bool(genre_whitelist.value),
                    "accepted_genres": [g for g in (accepted.value or []) if g and g.strip()],
                    "genre_mapping": {
                        f.value.strip(): t.value.strip()
                        for f, t, _row in mapping_rows
                        if f.value and f.value.strip() and t.value and t.value.strip()
                    },
                    "normalize_on_match": [f for f in (normalize_on_match.value or []) if f],
                    "source_order": [r["name"] for r in source_rows],
                    "disabled_sources": [
                        r["name"] for r in source_rows if not r["enabled"]
                    ],
                })
                controller.save_settings(new)
                ui.notify("Settings saved")
            except Exception:
                logger.exception("saving settings failed")
                ui.notify("Could not save settings (see logs)", type="negative")

        with ui.row().classes("w-full justify-end q-mt-sm"):
            ui.button("Save changes", icon="save", on_click=do_save).props("unelevated")
