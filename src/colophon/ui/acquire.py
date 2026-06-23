"""Acquire page: browse Real-Debrid audiobooks and download them into the library."""

from __future__ import annotations

import logging

from nicegui import ui

from colophon.controller import AppController
from colophon.ui.tabs import app_tabs
from colophon.ui.theme import apply_theme, dark_mode_button, setup_dark_mode

logger = logging.getLogger(__name__)


def _fmt_size(num_bytes: int) -> str:
    size = float(num_bytes)
    for unit in ("B", "KB", "MB", "GB"):
        if size < 1024:
            return f"{size:.0f} {unit}" if unit == "B" else f"{size:.1f} {unit}"
        size /= 1024
    return f"{size:.1f} TB"


def render_acquire(controller: AppController) -> None:
    apply_theme()
    dark = setup_dark_mode()
    with ui.header(elevated=True).classes("items-center q-px-md"):
        ui.icon("cloud_download", color="primary").classes("text-h5")
        ui.label("Acquire").classes("text-h6 q-ml-sm text-weight-medium")
        app_tabs(controller, "acquire")
        ui.space()
        dark_mode_button(dark)

    if not controller.rd_configured():
        with ui.card().classes("q-ma-md"):
            ui.label("Real-Debrid is not configured.").classes("text-subtitle1")
            ui.label("Add a Real-Debrid token in Settings to use acquisition.").classes(
                "text-caption text-grey-7"
            )
            ui.button(
                "Open Settings", icon="settings", on_click=lambda: ui.navigate.to("/settings")
            ).props("flat")
        return

    selected: set[str] = set()
    show_all = {"value": False}
    candidates: list = []

    with ui.column().classes("w-full q-pa-md gap-2"):
        with ui.row().classes("items-center w-full no-wrap q-gutter-sm"):
            load_btn = ui.button("Load torrents", icon="refresh")
            ui.switch("Show all (not just audiobooks)", on_change=lambda e: _set_show_all(e.value))
            ui.space()
            download_btn = ui.button("Download selected", icon="download")
            download_btn.set_enabled(False)
        list_box = ui.column().classes("w-full gap-0")
        progress_box = ui.column().classes("w-full gap-1 q-mt-sm")

    def _set_show_all(value: bool) -> None:
        show_all["value"] = value
        _render_list()

    def _visible() -> list:
        if show_all["value"]:
            return candidates
        return [c for c in candidates if c.is_audiobook]

    def _toggle(torrent_id: str, on: bool) -> None:
        if on:
            selected.add(torrent_id)
        else:
            selected.discard(torrent_id)
        download_btn.set_enabled(bool(selected))

    def _render_list() -> None:
        list_box.clear()
        visible = _visible()
        with list_box:
            if not visible:
                ui.label(
                    "No torrents loaded yet" if not candidates else "No matching torrents"
                ).classes("text-grey-6 q-pa-md")
                return
            with ui.list().props("separator dense").classes("w-full"):
                for cand in visible:
                    with ui.item():
                        with ui.item_section().props("avatar"):
                            ui.checkbox(
                                value=cand.torrent.id in selected,
                                on_change=lambda e, tid=cand.torrent.id: _toggle(tid, e.value),
                            ).props("dense")
                        with ui.item_section():
                            ui.item_label(cand.torrent.filename or "(unnamed)")
                            note = (
                                f"{len(cand.audio_files)}/{cand.total_files} audio file(s) "
                                f"- {_fmt_size(cand.torrent.bytes)}"
                            )
                            ui.item_label(note).props("caption")
                        if not cand.is_audiobook:
                            with ui.item_section().props("side"):
                                ui.badge("no audio").props("color=grey-6 outline")

    async def _load() -> None:
        load_btn.props("loading=true")
        try:
            result = await controller.rd_list_candidates()
        except Exception as e:  # surface listing failure to the operator (BLE001 intentional)
            logger.warning(f"RD listing failed: {e}")
            ui.notify("Could not load torrents (see logs)", type="negative")
            return
        finally:
            load_btn.props(remove="loading")
        candidates.clear()
        candidates.extend(result)
        selected.clear()
        download_btn.set_enabled(False)
        _render_list()

    async def _download() -> None:
        targets = list(selected)
        if not targets:
            return
        download_btn.props("loading=true")
        progress_box.clear()
        statuses: dict[str, ui.item_label] = {}
        with progress_box, ui.list().props("dense").classes("w-full"):
            for tid in targets:
                name = next((c.torrent.filename for c in candidates if c.torrent.id == tid), tid)
                with ui.item(), ui.item_section():
                    ui.item_label(name)
                    statuses[tid] = ui.item_label("waiting").props("caption")
        try:
            for tid in targets:
                statuses[tid].set_text("downloading...")

                def _on_progress(done: int, total: int, name: str, label=statuses[tid]) -> None:
                    label.set_text(f"downloading {done}/{total}: {name}")

                try:
                    result, book_ids = await controller.rd_download(tid, progress=_on_progress)
                    ok = sum(1 for f in result.files if f.ok)
                    if result.any_ok:
                        statuses[tid].set_text(
                            f"downloaded {ok} file(s), ingested {len(book_ids)} book(s)"
                        )
                    else:
                        statuses[tid].set_text("failed: no files downloaded")
                except Exception as e:  # isolate one torrent's failure (BLE001 intentional)
                    logger.warning(f"RD download failed for {tid}: {e}")
                    statuses[tid].set_text("failed (see logs)")
        finally:
            download_btn.props(remove="loading")
        ui.notify("Acquisition complete. New books are in the Library.")

    load_btn.on_click(_load)
    download_btn.on_click(_download)
    _render_list()
