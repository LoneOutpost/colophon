"""Acquisition service: list Real-Debrid audiobook torrents and download them.

Pure orchestration over the RD adapter and the streaming downloader. The handoff
to ingest is the controller's job (this stays free of repo dependencies)."""

from __future__ import annotations

import asyncio
import logging
import re
from collections import Counter
from collections.abc import Callable
from dataclasses import dataclass, field
from enum import StrEnum
from pathlib import Path, PurePosixPath

from colophon.adapters.audio import is_audio_file
from colophon.adapters.downloader import DownloadCancelled, stream_download
from colophon.adapters.realdebrid import (
    RdTorrent,
    RdTorrentFile,
    RdUnrestrictedLink,
    RealDebridSource,
)
from colophon.core.cancel import CancelToken

logger = logging.getLogger(__name__)

_READY_STATUS = "downloaded"
# RD statuses that mean the torrent is dead on arrival: no files will ever come from it.
_ERROR_STATUSES = frozenset({"error", "magnet_error", "dead", "virus"})
_COVER_EXTENSIONS = {".jpg", ".jpeg", ".png", ".webp"}
_MAX_NAME = 200  # keep a single path component well under the common 255-byte limit


class AcquireMode(StrEnum):
    """How a download resolves an existing target folder."""
    INDEXED = "indexed"      # allocate a fresh deduped folder (today's behavior)
    ADD = "add"              # write into the base folder; skip existing files, resume .part
    OVERWRITE = "overwrite"  # write into the base folder; replace each file cleanly


@dataclass
class AcquireCandidate:
    torrent: RdTorrent
    audio_files: list[RdTorrentFile]
    total_files: int
    is_ready: bool = True  # False for a torrent still preparing on RD (no file list yet)

    @property
    def is_audiobook(self) -> bool:
        return bool(self.audio_files)

    @property
    def is_errored(self) -> bool:
        """RD failed this torrent (dead magnet, removed, flagged): nothing to download."""
        return self.torrent.status in _ERROR_STATUSES


def visible_candidates(
    candidates: list[AcquireCandidate], *, show_all: bool
) -> list[AcquireCandidate]:
    """The candidates worth showing. `show_all` reveals everything (including errored and
    non-audiobook torrents); the default view hides errored ones and keeps just audiobooks
    plus still-preparing torrents (so a fresh add is visible while RD works on it)."""
    if show_all:
        return list(candidates)
    return [
        c for c in candidates
        if not c.is_errored and (c.is_audiobook or not c.is_ready)
    ]


@dataclass
class AcquiredFile:
    filename: str
    path: Path | None
    ok: bool
    error: str | None = None
    skipped: bool = False


@dataclass
class AcquireResult:
    folder: Path
    files: list[AcquiredFile] = field(default_factory=list)
    note: str | None = None  # a human-readable reason when the download can't proceed

    @property
    def any_ok(self) -> bool:
        return any(f.ok for f in self.files)


def _keep_file(name: str) -> bool:
    """Keep audio files (the audiobook) and cover images; skip everything else."""
    p = Path(name)
    return is_audio_file(p) or p.suffix.lower() in _COVER_EXTENSIONS


def sanitize_name(name: str) -> str:
    """A filesystem-safe version of `name`, clamped to a single path component's
    length (preserving any extension) and falling back to 'download' when empty."""
    cleaned = re.sub(r'[\\/:*?"<>|\x00-\x1f]', "_", name).strip().strip(".")
    if not cleaned:
        return "download"
    if len(cleaned) > _MAX_NAME:
        suffix = Path(cleaned).suffix
        suffix = suffix if len(suffix) <= 16 else ""  # ignore absurdly long "extensions"
        cleaned = cleaned[: _MAX_NAME - len(suffix)] + suffix
    return cleaned


def structured_dests(
    paths: list[str], dest_dir: Path, torrent_name: str, *,
    pinned: Path | None = None, mode: AcquireMode = AcquireMode.INDEXED,
) -> tuple[Path, list[Path]]:
    """Return (container, dests): reproduce each torrent-relative path faithfully under a
    container in `dest_dir`. When every path shares a top-level folder (the torrent's own
    root), that folder IS the container and files keep their full path below it. Bare files
    or mixed tops wrap in a `torrent_name` container. `pinned` reuses an existing container
    (resume). Result is 1:1 and order-preserving with `paths`; each component is sanitized."""
    comps = [list(PurePosixPath(p.strip("/")).parts) for p in paths]
    tops = {c[0] for c in comps if len(c) > 1}
    shared = next(iter(tops)) if len(tops) == 1 and all(len(c) > 1 for c in comps) else None
    if pinned is not None:
        container = pinned
    elif shared is not None:
        container = _container_for(dest_dir, shared, mode)
    else:
        container = _container_for(dest_dir, torrent_name, mode)
    rels = [c[1:] if shared is not None else c for c in comps]
    dests = [
        container.joinpath(*[sanitize_name(p) for p in rel]) if rel else container
        for rel in rels
    ]
    return container, dests


def _unique_dir(root: Path, name: str) -> Path:
    """A non-existing child of `root` based on `name` (deduped with -2, -3, ...)."""
    safe = sanitize_name(name)
    candidate = root / safe
    i = 2
    while candidate.exists():
        candidate = root / f"{safe}-{i}"
        i += 1
    return candidate


def _base_dir(root: Path, name: str) -> Path:
    """The un-suffixed child of `root` for `name` (reused if it already exists)."""
    return root / sanitize_name(name)


def _container_for(root: Path, name: str, mode: AcquireMode) -> Path:
    """The download container for `name` under `root`: a fresh deduped folder for INDEXED,
    the base folder (reused if present) for ADD/OVERWRITE."""
    return _unique_dir(root, name) if mode is AcquireMode.INDEXED else _base_dir(root, name)


async def list_candidates(client: RealDebridSource, *, limit: int = 100) -> list[AcquireCandidate]:
    """All RD torrents. Ready ones ('downloaded') carry their file list + audio classification
    and are pickable; in-progress ones are returned with their status/progress and no file list
    (not yet pickable). One torrent's info failing is isolated (logged), never aborting the list."""
    torrents = await client.list_torrents(limit)
    ready_ids = [t.id for t in torrents if t.status == _READY_STATUS]

    async def _info(tid: str):
        try:
            return await client.torrent_info(tid)
        except Exception:  # one torrent failing must not abort the listing (BLE001 intentional)
            logger.warning(f"torrent_info failed for {tid}", exc_info=True)
            return None

    infos = dict(zip(ready_ids, await asyncio.gather(*(_info(tid) for tid in ready_ids)), strict=True))
    candidates: list[AcquireCandidate] = []
    for t in torrents:
        info = infos.get(t.id)
        if info is not None:
            audio = [f for f in info.files if is_audio_file(Path(f.path))]
            candidates.append(AcquireCandidate(
                torrent=info, audio_files=audio, total_files=len(info.files), is_ready=True))
        else:
            candidates.append(AcquireCandidate(
                torrent=t, audio_files=[], total_files=0, is_ready=False))
    return candidates


async def _select_after_add(
    client: RealDebridSource, torrent_id: str, *, audio_only: bool
) -> None:
    """Tell RD which files to prepare for a freshly-added torrent. By default selects ALL
    files so RD caches the whole torrent (every file gets a link, so the picker and structure
    work). `audio_only=True` selects just the audio files, falling back to all when none are
    detected."""
    if not audio_only:
        await client.select_files(torrent_id, "all")
        return
    info = await client.torrent_info(torrent_id)
    audio_ids = [str(f.id) for f in info.files if is_audio_file(Path(f.path))]
    await client.select_files(torrent_id, ",".join(audio_ids) if audio_ids else "all")


async def add_torrent(client: RealDebridSource, magnet: str, *, audio_only: bool = False) -> str:
    """Add a magnet to Real-Debrid and select its files (see `_select_after_add`). Returns
    the new torrent id; it surfaces in `list_candidates` once ready."""
    torrent_id = await client.add_magnet(magnet)
    await _select_after_add(client, torrent_id, audio_only=audio_only)
    return torrent_id


async def add_torrent_file(
    client: RealDebridSource, content: bytes, *, audio_only: bool = False
) -> str:
    """Upload a .torrent file's bytes to Real-Debrid and select its files (see
    `_select_after_add`). Returns the new torrent id; it surfaces in `list_candidates`
    once ready."""
    torrent_id = await client.add_torrent_file(content)
    await _select_after_add(client, torrent_id, audio_only=audio_only)
    return torrent_id


def plan_pairs(
    torrent: RdTorrent, file_ids: set[int] | None
) -> tuple[list[tuple[str, str]] | None, set[str] | None]:
    """Pair each RD download link with its selected file's torrent path.

    Returns (pairs, keep_basenames):
    - pairs is a list of (path, link) when RD's `links[i]` <-> `selected[i]` contract
      holds (equal counts, a non-empty file list). `file_ids` filters to chosen ids.
    - pairs is None when there is no usable file list or the counts disagree — the
      caller falls back to the flat link list. keep_basenames is then None (keep the
      audio+cover default when file_ids is None) or the chosen files' basenames."""
    selected = [f for f in getattr(torrent, "files", []) if f.selected]
    links = list(torrent.links)
    if selected and len(selected) == len(links):
        pairs = [
            (f.path, links[i])
            for i, f in enumerate(selected)
            if file_ids is None or f.id in file_ids
        ]
        return pairs, None
    logger.warning(
        f"plan_pairs: links/selected mismatch ({len(links)} vs {len(selected)}); flat fallback"
    )
    if file_ids is None:
        return None, None
    keep = {Path(f.path).name for f in selected if f.id in file_ids}
    return None, keep


def _fallback_dest_map(
    selected: list[RdTorrentFile], file_ids: set[int] | None,
    dest_root: Path, torrent_name: str, pinned: Path | None,
    *, mode: AcquireMode = AcquireMode.INDEXED,
) -> tuple[Path, dict[str, Path], set[str]]:
    """For the count-mismatch path (RD returns fewer links than selected files): pick the target
    files (by `file_ids`, else the audio+cover default), compute their structured destinations,
    and index them by basename so each link can be matched to its real path via the unrestricted
    filename. Returns (container, dest_by_name, target_names). A basename shared by more than one
    target is omitted from `dest_by_name` — it can't be disambiguated by filename alone and the
    caller writes it flat."""
    if file_ids is not None:
        targets = [f for f in selected if f.id in file_ids]
    else:
        targets = [f for f in selected if _keep_file(f.path)]
    container, dests = structured_dests(
        [f.path for f in targets], dest_root, torrent_name, pinned=pinned, mode=mode)
    names = [PurePosixPath(f.path).name for f in targets]
    counts = Counter(names)
    dest_by_name = {n: d for n, d in zip(names, dests, strict=True) if counts[n] == 1}
    return container, dest_by_name, set(names)


async def download_torrent(
    client: RealDebridSource,
    torrent: RdTorrent,
    dest_root: Path,
    *,
    folder: Path | None = None,
    file_ids: set[int] | None = None,
    progress: Callable[[int, int, str], None] | None = None,
    byte_progress: Callable[[int, int], None] | None = None,
    cancel: CancelToken | None = None,
    mode: AcquireMode = AcquireMode.INDEXED,
) -> AcquireResult:
    """Unrestrict each of `torrent`'s links and stream the audio/cover files into a
    subfolder of `dest_root`. Per-file failures are isolated and reported.

    `folder` pins the destination: pass the folder from an interrupted attempt to
    resume into it (so `stream_download` finds the retained `.part`); when omitted a
    fresh deduped subfolder is allocated. `progress(done, total, filename)` reports
    per-file granularity (file index of total links), not per-byte; that is the
    granularity the acquire UI surfaces."""
    selected = [f for f in getattr(torrent, "files", []) if f.selected]
    pairs, _ = plan_pairs(torrent, file_ids)
    if pairs is not None:
        # RD's contract held (links[i] <-> selected[i]): map by index, using the torrent path.
        container, dests = structured_dests(
            [p for p, _ in pairs], dest_root, torrent.filename, pinned=folder, mode=mode)
    elif selected:
        # RD returned a different number of links than selected files (a known quirk for large
        # torrents). Recover structure by matching each link's unrestricted filename to a selected
        # file's real path — not flattening.
        container, dest_by_name, target_names = _fallback_dest_map(
            selected, file_ids, dest_root, torrent.filename, folder, mode=mode)
    else:
        container = folder or _container_for(dest_root, torrent.filename, mode)  # no file list: flat default
    container.mkdir(parents=True, exist_ok=True)
    result = AcquireResult(folder=container)

    async def _fetch(url: str, dest: Path, display: str) -> bool:
        """Stream one file; record the outcome. Returns False to stop the batch (cancel)."""
        part = dest.with_name(dest.name + ".part")
        if mode is AcquireMode.ADD and dest.exists():
            result.files.append(
                AcquiredFile(filename=display, path=dest, ok=True, skipped=True))
            return True  # already present; keep it, don't re-fetch
        if mode is AcquireMode.OVERWRITE:
            dest.unlink(missing_ok=True)
            part.unlink(missing_ok=True)  # drop any stale partial so the re-fetch is clean
        try:
            dest.parent.mkdir(parents=True, exist_ok=True)
            await stream_download(url, dest, progress=byte_progress, cancel=cancel)
            result.files.append(AcquiredFile(filename=display, path=dest, ok=True))
        except DownloadCancelled:
            result.files.append(AcquiredFile(filename=display, path=None, ok=False, error="cancelled"))
            return False  # stop the batch on cancel; the .part is retained for resume
        except Exception as e:  # isolate a single failed download (BLE001 intentional)
            logger.warning(f"download failed for {display}: {e}")
            result.files.append(AcquiredFile(filename=display, path=None, ok=False, error=str(e)))
        return True

    async def _unrestrict(link: str, label: str) -> RdUnrestrictedLink | None:
        try:
            return await client.unrestrict_link(link)
        except Exception as e:  # one link failing must not abort the batch (BLE001 intentional)
            logger.warning(f"unrestrict failed: {e}")
            result.files.append(AcquiredFile(filename=label, path=None, ok=False, error=str(e)))
            return None

    if pairs is not None:
        total = len(pairs)
        for idx, ((path, link), dest) in enumerate(zip(pairs, dests, strict=True), start=1):
            unr = await _unrestrict(link, path)
            if unr is None:
                continue
            if progress is not None:
                progress(idx, total, PurePosixPath(path).name)
            if not await _fetch(unr.download, dest, PurePosixPath(path).name):
                break
    elif selected:
        # Count mismatch: iterate links, mapping each to a target file's path by basename.
        links = list(torrent.links)
        total = len(links)
        for idx, link in enumerate(links, start=1):
            unr = await _unrestrict(link, link)
            if unr is None:
                continue
            name = PurePosixPath(unr.filename).name
            if name not in target_names:
                continue  # this link isn't one of the picked/target files
            dest = dest_by_name.get(name)
            if dest is None:  # a basename shared by >1 target — can't place it safely, write flat
                logger.warning(f"ambiguous basename {name!r} under count mismatch; writing flat")
                dest = container / sanitize_name(name)
            if progress is not None:
                progress(idx, total, name)
            if not await _fetch(unr.download, dest, name):
                break
    else:
        # No file list at all: keep the audio+cover default, flat by basename.
        links = list(torrent.links)
        total = len(links)
        for idx, link in enumerate(links, start=1):
            unr = await _unrestrict(link, link)
            if unr is None:
                continue
            if not _keep_file(unr.filename):
                continue
            if progress is not None:
                progress(idx, total, unr.filename)
            if not await _fetch(unr.download, container / sanitize_name(unr.filename), unr.filename):
                break
    if not result.any_ok and len(torrent.links) == 1 and len(selected) > 1:
        # Real-Debrid handed back a single link for a many-file torrent and it matched none
        # of the picked files: RD is serving the whole torrent as one archive (its RAR quirk),
        # so per-file picks can't be fulfilled. Say so plainly instead of a bare "failed".
        result.note = (
            f"Real-Debrid is serving this torrent as a single archive "
            f"({len(selected)} files bundled into one link), so individual files can't be "
            f"downloaded. Re-add it on Real-Debrid, or use a source that keeps files separate."
        )
    if not result.any_ok:
        # Nothing landed; drop the staging dir if it is empty (leave it if .part
        # remnants remain, so a retry/cleanup can still find them).
        try:
            container.rmdir()
        except OSError:
            pass
    return result
