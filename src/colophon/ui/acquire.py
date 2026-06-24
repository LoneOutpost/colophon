"""Acquire page: browse Real-Debrid audiobooks and download them into the library."""

from __future__ import annotations

import logging

from nicegui import background_tasks, ui

from colophon.controller import AppController
from colophon.services.filetree import (
    FolderNode,
    build_file_tree,
    default_selection,
)
from colophon.ui.chrome import page_header

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


def _candidate_caption(cand, picks: set[int], tree: list[FolderNode]) -> str:
    base = (
        f"{len(cand.audio_files)}/{cand.total_files} audio file(s) "
        f"· {_fmt_size(cand.torrent.bytes)}"
    )
    if not picks:
        return base
    chosen = sum(f.bytes for node in tree for f in node.files if f.id in picks)
    return f"{base}    ·    {len(picks)} file(s) · {_fmt_size(chosen)} selected"


def _folder_caption(node: FolderNode, picks: set[int]) -> str:
    name = node.name or "(root)"
    sel = sum(1 for f in node.files if f.id in picks)
    state = f"{sel}/{node.count} selected" if sel else f"{node.count} file(s)"
    return f"{name}    ·    {state} · {_fmt_size(node.total_bytes)}"


def render_acquire(controller: AppController) -> None:
    with page_header(controller, "acquire", icon="cloud_download", label="Acquire"):
        pass

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

    # selection state
    file_picks: dict[str, set[int]] = {}      # torrent id -> chosen file ids
    trees: dict[str, list[FolderNode]] = {}   # torrent id -> built folder tree (cached)
    refs: dict[str, dict] = {}                # torrent id -> live widget refs for in-place updates
    suppress = {"on": False}                  # guard against set_value feedback loops

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

    # selection helpers -------------------------------------------------------
    def _all_ids(tid: str) -> set[int]:
        return {f.id for node in trees.get(tid, []) for f in node.files}

    def _picks(tid: str) -> set[int]:
        return file_picks.setdefault(tid, set())

    def _refresh(tid: str) -> None:
        """Update derived widgets (master/folder checkboxes, captions, button) in
        place from `file_picks` without rebuilding the tree."""
        r = refs.get(tid)
        if r:
            picks = _picks(tid)
            tree = trees.get(tid, [])
            all_ids = _all_ids(tid)
            suppress["on"] = True
            r["total"].set_text(_candidate_caption(r["cand"], picks, tree))
            r["master"].set_value(bool(all_ids) and picks >= all_ids)
            for fr in r["folders"].values():
                ids = fr["ids"]
                fr["cb"].set_value(bool(ids) and ids <= picks)
                fr["label"].set_text(_folder_caption(fr["node"], picks))
                for fid, cb in fr["file_cbs"].items():
                    cb.set_value(fid in picks)
            suppress["on"] = False
        download_btn.set_enabled(any(p for p in file_picks.values()))

    def _set_file(tid: str, file_id: int, on: bool) -> None:
        if suppress["on"]:
            return
        (_picks(tid).add if on else _picks(tid).discard)(file_id)
        _refresh(tid)

    def _set_folder(tid: str, node: FolderNode, on: bool) -> None:
        if suppress["on"]:
            return
        ids = {f.id for f in node.files}
        if on:
            _picks(tid).update(ids)
        else:
            _picks(tid).difference_update(ids)
        _refresh(tid)

    def _select_all(tid: str, on: bool) -> None:
        file_picks[tid] = set(_all_ids(tid)) if on else set()
        _refresh(tid)

    # rendering ---------------------------------------------------------------
    def _render_tree(tid: str, tree: list[FolderNode]) -> None:
        picks = _picks(tid)
        r = refs[tid]
        for node in tree:
            ids = {f.id for f in node.files}
            fr: dict = {"node": node, "ids": ids, "file_cbs": {}, "built": {"on": False}}
            r["folders"][node.name] = fr
            f_exp = ui.expansion().props("dense expand-icon-toggle").classes("w-full")
            with f_exp.add_slot("header"):
                with ui.row().classes("items-center w-full no-wrap gap-2"):
                    cb = ui.checkbox(
                        value=bool(ids) and ids <= picks,
                        on_change=lambda e, t=tid, nd=node: _set_folder(t, nd, e.value),
                    ).props("dense")
                    cb.on("click.stop")
                    fr["cb"] = cb
                    fr["label"] = ui.label(_folder_caption(node, picks)).props("caption")
            fbody = ui.column().classes("w-full gap-0 q-pl-lg")

            def _build_files(open_state: bool, body=fbody, nd=node, t=tid, frr=fr) -> None:
                if frr["built"]["on"] or not open_state:
                    return
                frr["built"]["on"] = True
                pk = _picks(t)
                with body:
                    for f in nd.files:
                        frr["file_cbs"][f.id] = ui.checkbox(
                            f"{f.name}    {_fmt_size(f.bytes)}",
                            value=f.id in pk,
                            on_change=lambda e, tt=t, fid=f.id: _set_file(tt, fid, e.value),
                        ).props("dense")

            f_exp.on_value_change(lambda e, fn=_build_files: fn(e.value))

    def _render_candidate(cand) -> None:
        tid = cand.torrent.id
        tree = trees[tid]
        picks = _picks(tid)
        all_ids = _all_ids(tid)
        r: dict = {"cand": cand, "folders": {}}
        refs[tid] = r

        exp = ui.expansion().props("dense expand-icon-toggle").classes("w-full")
        with exp.add_slot("header"):
            with ui.row().classes("items-center w-full no-wrap gap-2"):
                r["master"] = ui.checkbox(
                    value=bool(all_ids) and picks >= all_ids,
                    on_change=lambda e, t=tid: None if suppress["on"] else _select_all(t, e.value),
                ).props("dense")
                r["master"].on("click.stop")
                with ui.column().classes("col gap-0"):
                    ui.label(cand.torrent.filename or "(unnamed)")
                    r["total"] = ui.label(_candidate_caption(cand, picks, tree)).props("caption")
                if not cand.is_audiobook:
                    ui.badge("no audio").props("color=grey-6 outline")
                ui.button("All", on_click=lambda _e, t=tid: _select_all(t, True)).props(
                    "flat dense"
                ).on("click.stop")
                ui.button("None", on_click=lambda _e, t=tid: _select_all(t, False)).props(
                    "flat dense"
                ).on("click.stop")
        body = ui.column().classes("w-full gap-0 q-pl-md")
        built = {"on": False}

        def _build_body(open_state: bool, container=body, t=tid, tr=tree, b=built) -> None:
            if b["on"] or not open_state:
                return
            b["on"] = True
            with container:
                _render_tree(t, tr)

        exp.on_value_change(lambda e, fn=_build_body: fn(e.value))

    def _render_list() -> None:
        list_box.clear()
        refs.clear()
        visible = _visible()
        with list_box:
            if not visible:
                ui.label(
                    "No torrents loaded yet" if not candidates else "No matching torrents"
                ).classes("text-grey-6 q-pa-md")
            else:
                for cand in visible:
                    tid = cand.torrent.id
                    if tid not in trees:
                        trees[tid] = build_file_tree(cand.torrent.files)
                        file_picks[tid] = default_selection(trees[tid])
                    _render_candidate(cand)
        download_btn.set_enabled(any(p for p in file_picks.values()))

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
        file_picks.clear()
        trees.clear()
        refs.clear()
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

    async def _run_one(tid: str, name: str, file_ids: list[int]) -> None:
        try:
            await controller.rd_download(tid, name=name, file_ids=file_ids)
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
        targets = [tid for tid, picks in file_picks.items() if picks]
        if not targets:
            return
        for tid in targets:
            name = next((c.torrent.filename for c in candidates if c.torrent.id == tid), tid)
            background_tasks.create(_run_one(tid, name, sorted(file_picks[tid])))
        download_btn.set_enabled(False)
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
