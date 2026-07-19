"""Acquisition service: list Real-Debrid audiobook torrents and download them.

Pure orchestration over the RD adapter and the streaming downloader. The handoff
to ingest is the controller's job (this stays free of repo dependencies)."""

from __future__ import annotations

import asyncio
import logging
import re
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

# progress(phase, done, total, name); phase is "resolving" or "downloading".
PhaseProgress = Callable[[str, int, int, str], None]

# RD statuses whose torrents carry a usable file list + live links, so they are pickable now.
# "uploading" is RD copying an already-finished torrent to its own hosts: the files and links
# are populated and retrievable well before the status flips to "downloaded".
_READY_STATUSES = frozenset({"downloaded", "uploading"})
# RD statuses that mean the torrent is dead on arrival: no files will ever come from it.
_ERROR_STATUSES = frozenset({"error", "magnet_error", "dead", "virus"})
_COVER_EXTENSIONS = {".jpg", ".jpeg", ".png", ".webp"}
_MAX_NAME = 200  # keep a single path component well under the common 255-byte limit
_RESOLVE_CONCURRENCY = 8  # global cap on in-flight RD unrestrict calls (shared across downloads)


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
    """Return (container, dests): reproduce each torrent-relative path faithfully under one
    `torrent_name` container in `dest_dir`. Files keep their full torrent-relative path below the
    container, so a picked subfolder is preserved. The single exception: when every path shares one
    top-level folder AND that folder just duplicates the torrent name, drop it so we don't nest
    name/name/…. `pinned` reuses an existing container (resume / a follow-up pick). Result is 1:1
    and order-preserving with `paths`; each component is sanitized.

    The strip decision depends only on the paths and the torrent name (never on `pinned`), so a
    resume reproduces identical destinations. Naming the container after a shared top folder was the
    old behavior; it flattened a subfolder pick into a pinned container's root, so it was removed."""
    comps = [list(PurePosixPath(p.strip("/")).parts) for p in paths]
    container = pinned if pinned is not None else _container_for(dest_dir, torrent_name, mode)
    tops = {c[0] for c in comps if len(c) > 1}
    shared = next(iter(tops)) if len(tops) == 1 and all(len(c) > 1 for c in comps) else None
    strip = shared is not None and sanitize_name(shared) == sanitize_name(torrent_name)
    rels = [c[1:] if strip else c for c in comps]
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
    """All RD torrents. Ready ones ('downloaded'/'uploading') carry their file list + audio
    classification and are pickable; in-progress ones are returned with their status/progress and
    no file list (not yet pickable). One torrent's info failing is isolated (logged), never
    aborting the list."""
    torrents = await client.list_torrents(limit)
    ready_ids = [t.id for t in torrents if t.status in _READY_STATUSES]

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


def download_target_count(torrent: RdTorrent, file_ids: set[int] | None) -> int:
    """The number of files a download will actually target (its progress denominator).

    An explicit pick counts the chosen ids; download-all counts the selected files that
    survive the audio+cover keep-filter; a torrent with no file list counts its links
    (the only case where a link is the unit). Never counts raw links otherwise, so the
    UI can't show '1638 files' for a handful of picked ones."""
    selected = [f for f in getattr(torrent, "files", []) if f.selected]
    if not selected:
        return len(torrent.links)
    if file_ids is not None:
        return sum(1 for f in selected if f.id in file_ids)
    return sum(1 for f in selected if _keep_file(f.path))


def align_links_to_files(
    files: list[tuple[str, int]], links: list[tuple[str, int]]
) -> list[int | None]:
    """Map each link to the index of its selected file for the count-mismatch path. Real-Debrid
    returns download links as a clean in-order *subsequence* of the selected files (some selected
    files simply get no link), so walk both in order with a single forward pointer: each link
    claims the next not-yet-claimed file whose basename matches. Resolution priority:

    1. Exact (basename, size) match — jumps past any gap files that had no link.
    2. Closest-size match among all forward same-basename candidates — only used when the link's
       size is usable (> 0); corrects for a missing earlier same-named file without trusting
       position alone.
    3. First forward basename match by position — always safe; used when size is 0/garbage.

    `files` and `links` are (basename, size) pairs in order; returns one index into `files` per
    link, or None when no forward basename match remains (a true orphan, placed without clobbering
    by the caller)."""
    out: list[int | None] = []
    n = len(files)
    i = 0
    for lname, lsize in links:
        exact = -1
        first_name = -1        # first same-basename by position
        closest = -1           # same-basename with size nearest lsize (when lsize usable)
        closest_delta = -1
        j = i
        while j < n:
            fname, fsize = files[j]
            if fname == lname:
                if fsize == lsize:
                    exact = j
                    break
                if first_name == -1:
                    first_name = j
                if lsize > 0:
                    delta = abs(fsize - lsize)
                    if closest == -1 or delta < closest_delta:
                        closest, closest_delta = j, delta
            j += 1
        hit = exact if exact != -1 else (closest if closest != -1 else first_name)
        out.append(None if hit == -1 else hit)
        if hit != -1:
            i = hit + 1
    return out


async def _resolve_links(
    client: RealDebridSource,
    links: list[str],
    *,
    sem: asyncio.Semaphore | None,
    cancel: CancelToken | None,
    on_progress: Callable[[int, int], None] | None = None,
) -> list[RdUnrestrictedLink | None]:
    """Unrestrict every link, preserving order (result[i] <-> links[i]); a failed or
    cancelled link becomes None. `sem` bounds concurrency globally (falls back to a
    local cap). `on_progress(done, total)` fires as each link settles."""
    gate = sem or asyncio.Semaphore(_RESOLVE_CONCURRENCY)
    results: list[RdUnrestrictedLink | None] = [None] * len(links)
    total = len(links)
    done = 0

    async def _one(i: int, link: str) -> None:
        nonlocal done
        async with gate:
            if cancel is not None and cancel.cancelled:
                done += 1
                if on_progress is not None:
                    on_progress(done, total)
                return
            try:
                results[i] = await client.unrestrict_link(link)
            except Exception as e:  # one link failing must not abort the batch (BLE001 intentional)
                logger.warning(f"unrestrict failed: {e}")
            done += 1
            if on_progress is not None:
                on_progress(done, total)

    await asyncio.gather(*(_one(i, link) for i, link in enumerate(links)))
    return results


async def download_torrent(
    client: RealDebridSource,
    torrent: RdTorrent,
    dest_root: Path,
    *,
    folder: Path | None = None,
    file_ids: set[int] | None = None,
    progress: PhaseProgress | None = None,
    byte_progress: Callable[[int, int], None] | None = None,
    cancel: CancelToken | None = None,
    mode: AcquireMode = AcquireMode.INDEXED,
    resolve_sem: asyncio.Semaphore | None = None,
) -> AcquireResult:
    """Resolve `torrent`'s links then stream the picked audio/cover files into a
    subfolder of `dest_root`, preserving the torrent's directory structure. Two phases
    drive `progress(phase, done, total, name)`: 'resolving' (unrestricting links) then
    'downloading' (streaming files); `total` in the download phase is the picked-file
    count, never the raw link count. `folder` pins the destination for resume; `mode`
    controls conflict handling; `resolve_sem` bounds unrestrict concurrency globally."""
    selected = [f for f in getattr(torrent, "files", []) if f.selected]
    pairs, _ = plan_pairs(torrent, file_ids)

    def _resolve_progress(done: int, total: int) -> None:
        if progress is not None:
            progress("resolving", done, total, "")

    # --- PHASE 1: resolve links, recover (dest, url, name) to download ---
    dl_plan: list[tuple[Path, str, str]] = []
    if pairs is not None:
        # RD contract held: links[i] <-> selected[i]. Resolve in order, map by index.
        unrs = await _resolve_links(
            client, [link for _, link in pairs], sem=resolve_sem, cancel=cancel,
            on_progress=_resolve_progress)
        kept = [(path, unr) for (path, _), unr in zip(pairs, unrs, strict=True) if unr is not None]
        container, dests = structured_dests(
            [p for p, _ in kept], dest_root, torrent.filename, pinned=folder, mode=mode)
        for (path, unr), dest in zip(kept, dests, strict=True):
            dl_plan.append((dest, unr.download, PurePosixPath(path).name))
    elif selected:
        # Count mismatch: resolve all links, then map each to its file by ORDER.
        unrs = await _resolve_links(
            client, list(torrent.links), sem=resolve_sem, cancel=cancel,
            on_progress=_resolve_progress)
        resolved = [u for u in unrs if u is not None]
        idxs = align_links_to_files(
            [(PurePosixPath(f.path).name, f.bytes) for f in selected],
            [(PurePosixPath(u.filename).name, u.filesize) for u in resolved],
        )
        picked: list[tuple[RdTorrentFile, RdUnrestrictedLink]] = []
        orphans = 0
        for u, hit in zip(resolved, idxs, strict=True):
            if hit is None:
                orphans += 1
                continue
            f = selected[hit]
            if (f.id in file_ids) if file_ids is not None else _keep_file(f.path):
                picked.append((f, u))
        if orphans:
            logger.warning(f"{orphans} link(s) matched no selected file; dropped")
        container, dests = structured_dests(
            [f.path for f, _ in picked], dest_root, torrent.filename, pinned=folder, mode=mode)
        for (f, unr), dest in zip(picked, dests, strict=True):
            dl_plan.append((dest, unr.download, PurePosixPath(f.path).name))
    else:
        # No file list: resolve all links, keep audio+cover, flat by basename.
        container = folder or _container_for(dest_root, torrent.filename, mode)
        unrs = await _resolve_links(
            client, list(torrent.links), sem=resolve_sem, cancel=cancel,
            on_progress=_resolve_progress)
        for unr in unrs:
            if unr is None or not _keep_file(unr.filename):
                continue
            name = PurePosixPath(unr.filename).name
            dl_plan.append((container / sanitize_name(name), unr.download, name))

    container.mkdir(parents=True, exist_ok=True)
    result = AcquireResult(folder=container)

    async def _fetch(url: str, dest: Path, display: str) -> bool:
        """Stream one file; record the outcome. Returns False to stop the batch (cancel)."""
        part = dest.with_name(dest.name + ".part")
        if mode is AcquireMode.ADD and dest.exists():
            result.files.append(AcquiredFile(filename=display, path=dest, ok=True, skipped=True))
            return True
        if mode is AcquireMode.OVERWRITE:
            dest.unlink(missing_ok=True)
            part.unlink(missing_ok=True)
        try:
            dest.parent.mkdir(parents=True, exist_ok=True)
            await stream_download(url, dest, progress=byte_progress, cancel=cancel)
            result.files.append(AcquiredFile(filename=display, path=dest, ok=True))
        except DownloadCancelled:
            result.files.append(AcquiredFile(filename=display, path=None, ok=False, error="cancelled"))
            return False
        except Exception as e:  # isolate a single failed download (BLE001 intentional)
            logger.warning(f"download failed for {display}: {e}")
            result.files.append(AcquiredFile(filename=display, path=None, ok=False, error=str(e)))
        return True

    # --- PHASE 2: download the picked files ---
    total = len(dl_plan)
    for idx, (dest, url, name) in enumerate(dl_plan, start=1):
        if progress is not None:
            progress("downloading", idx, total, name)
        if not await _fetch(url, dest, name):
            break

    if not result.any_ok and len(torrent.links) == 1 and len(selected) > 1:
        result.note = (
            f"Real-Debrid is serving this torrent as a single archive "
            f"({len(selected)} files bundled into one link), so individual files can't be "
            f"downloaded. Re-add it on Real-Debrid, or use a source that keeps files separate."
        )
    if not result.any_ok:
        try:
            container.rmdir()
        except OSError:
            pass
    return result
