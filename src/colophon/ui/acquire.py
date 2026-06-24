"""Acquire page: browse Real-Debrid audiobooks and download them into the library."""

from __future__ import annotations

import logging

from nicegui import background_tasks, ui

from colophon.controller import AppController
from colophon.ui.tabs import app_tabs
from colophon.ui.theme import apply_theme, dark_mode_button, setup_dark_mode

logger = logging.getLogger(__name__)

# status -> (Quasar colour, icon) for a download row
_STATUS_META = {
    "active": ("primary", "downloading"),
    "paused": ("orange", "pause_circle"),
    "done": ("positive", "check_circle"),
    "failed": ("negative", "error"),
}


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
    scan_prompt = {"shown": False}

    with ui.column().classes("w-full q-pa-md gap-2"):
        with ui.row().classes("items-center w-full no-wrap q-gutter-sm"):
            magnet_input = (
                ui.input(placeholder="Paste a magnet link").props("dense clearable").classes("col")
            )
            add_btn = ui.button("Add", icon="add")
        with ui.row().classes("items-center w-full no-wrap q-gutter-sm"):
            load_btn = ui.button("Load torrents", icon="refresh")
            ui.switch("Show all (not just audiobooks)", on_change=lambda e: _set_show_all(e.value))
            ui.space()
            download_btn = ui.button("Download selected", icon="download")
            download_btn.set_enabled(False)
        list_box = ui.column().classes("w-full gap-0")
        with ui.row().classes("items-center w-full q-mt-md"):
            ui.label("Downloads").classes("text-subtitle1 text-weight-medium")
            ui.space()
            ui.button("Clear finished", icon="clear_all", on_click=lambda: _clear_finished()).props(
                "flat dense"
            )
        downloads_box = ui.column().classes("w-full gap-0")

    # --- magnet ---
    async def _add_magnet() -> None:
        magnet = (magnet_input.value or "").strip()
        if not magnet:
            ui.notify("Paste a magnet link first", type="warning")
            return
        add_btn.props("loading=true")
        try:
            await controller.rd_add_magnet(magnet)
        except Exception as e:  # surface add failure to the operator (BLE001 intentional)
            logger.warning(f"RD add magnet failed: {e}")
            ui.notify("Could not add magnet (see logs)", type="negative")
            return
        finally:
            add_btn.props(remove="loading")
        magnet_input.set_value("")
        ui.notify("Added. It will appear here once Real-Debrid finishes preparing it.")

    # --- candidate list ---
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

    # --- downloads (registry-driven, polled) ---
    def _render_downloads() -> None:
        downloads_box.clear()
        entries = controller.active_downloads()
        with downloads_box:
            if not entries:
                ui.label("No downloads yet").classes("text-grey-6 q-pa-md")
                return
            with ui.list().props("separator dense").classes("w-full"):
                for entry in entries:
                    color, icon = _STATUS_META.get(entry.status, ("grey-6", "help"))
                    with ui.item():
                        with ui.item_section().props("avatar"):
                            ui.icon(icon, color=color)
                        with ui.item_section():
                            ui.item_label(entry.name or "(unnamed)")
                            detail = f"{entry.status} · {entry.detail}" if entry.detail else entry.status
                            ui.item_label(detail).props("caption")
                        if entry.status in ("active", "paused"):
                            with ui.item_section().props("side"):
                                if entry.status == "active":
                                    ui.button(
                                        icon="close",
                                        on_click=lambda _e, k=entry.key: controller.cancel_download(k),
                                    ).props("flat dense round").tooltip("Cancel")
                                else:
                                    ui.button(
                                        icon="play_arrow",
                                        on_click=lambda _e, k=entry.key: _resume(k),
                                    ).props("flat dense round").tooltip("Resume")

    def _clear_finished() -> None:
        controller.clear_finished_downloads()
        _render_downloads()

    async def _run_one(tid: str, name: str) -> None:
        try:
            await controller.rd_download(tid, name=name)
        except Exception as e:  # isolate one torrent's failure (BLE001 intentional)
            logger.warning(f"RD download failed for {tid}: {e}")

    async def _resume_one(key: str) -> None:
        try:
            await controller.resume_download(key)
        except Exception as e:  # isolate one resume failure (BLE001 intentional)
            logger.warning(f"RD resume failed for {key}: {e}")

    def _resume(key: str) -> None:
        background_tasks.create(_resume_one(key))
        _render_downloads()

    def _download() -> None:
        targets = list(selected)
        if not targets:
            return
        for tid in targets:
            name = next((c.torrent.filename for c in candidates if c.torrent.id == tid), tid)
            background_tasks.create(_run_one(tid, name))
        selected.clear()
        download_btn.set_enabled(False)
        _render_list()
        _render_downloads()
        ui.notify("Downloading. Progress appears under Downloads below.")

    # --- one-time scan-path prompt (after a download completes) ---
    def _maybe_prompt_scan() -> None:
        if scan_prompt["shown"] or not controller.should_prompt_downloads_scan():
            return
        if not any(e.status == "done" for e in controller.active_downloads()):
            return
        scan_prompt["shown"] = True
        with ui.dialog() as dialog, ui.card().classes("w-96"):
            ui.label("Add the downloads folder to your scan paths?").classes("text-subtitle1")
            ui.label(
                "New downloads will then be picked up the next time you scan your library."
            ).classes("text-caption text-grey-7")

            def _not_now() -> None:
                controller.mark_downloads_scan_prompt_seen()
                dialog.close()

            def _add() -> None:
                controller.add_downloads_to_scan_paths()
                dialog.close()
                ui.notify("Added the downloads folder to your scan paths.")

            with ui.row().classes("w-full justify-end q-gutter-sm q-mt-sm"):
                ui.button("Not now", on_click=_not_now).props("flat")
                ui.button("Add", icon="add", on_click=_add).props("unelevated")
        dialog.open()

    def _tick() -> None:
        _render_downloads()
        _maybe_prompt_scan()

    add_btn.on_click(_add_magnet)
    magnet_input.on("keydown.enter", _add_magnet)
    load_btn.on_click(_load)
    download_btn.on_click(_download)
    _render_list()
    _render_downloads()
    ui.timer(1.0, _tick)
