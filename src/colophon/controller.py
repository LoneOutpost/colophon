"""UI-agnostic orchestration of the Colophon pipeline. The UI calls only this."""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Callable, Iterable
from pathlib import Path
from typing import ClassVar

from colophon.adapters.audio import is_audio_file
from colophon.adapters.config import PATTERN_HISTORY_CAP, Config, OrganizePattern, save_config
from colophon.adapters.cover import mime_for_suffix
from colophon.adapters.downloader import (
    DownloadCancelled,  # noqa: F401 - re-exported for the Acquire UI
)
from colophon.adapters.lazylibrarian import AudiobookPatterns, read_audiobook_patterns
from colophon.adapters.realdebrid import RdUser, RealDebridClient
from colophon.adapters.sidecar import write_sidecar
from colophon.app_context import AppContext, build_all_sources, default_db_path
from colophon.core.cancel import CancelToken
from colophon.core.catalog import CatalogEntry, list_entries
from colophon.core.chapters import Chapter, normalize_chapters, runtime_mismatch
from colophon.core.confidence import IdentificationOutcome, score_identification
from colophon.core.fields import get_field
from colophon.core.filename_parser import compile_template, parse_filename
from colophon.core.genre_policy import GenrePolicy
from colophon.core.models import (
    BookState,
    BookUnit,
    ConfidenceSignal,
    EditChange,
    Finding,
    FindingCode,
    FindingSeverity,
    OperationRecord,
    Phase,
    PhaseState,
    Provenance,
    _Base,
    new_batch_id,
)
from colophon.core.navigator import (
    DirectoryListing,
    DirEntry,
    LibraryTree,
    build_library_tree,
)
from colophon.core.normalize import FIELD_NORMALIZERS, merge_preserve, normalize_genres
from colophon.core.pathscheme import build_target_path
from colophon.core.phases import LOCAL, ensure_phases, invalidate_from, mark, resync_state, state_of
from colophon.core.quickmatch import (
    IdentifyPlan,
    IdentifySummary,
    QuickMatchProposal,
    QuickMatchSummary,
)
from colophon.core.sources import MetadataSource, SourceQuery, SourceResult, arrange_sources
from colophon.services import files as file_ops
from colophon.services.acquire import (
    AcquireCandidate,
    AcquireResult,
    add_torrent,
    download_torrent,
    list_candidates,
    sanitize_name,
)
from colophon.services.catalog import apply_catalog_mapping
from colophon.services.cover import ensure_cached_cover, thumbnail_bytes
from colophon.services.editing import (
    apply_fields,
    bulk_apply_fields,
    remap_field,
    set_field_value,
)
from colophon.services.editing import (
    bulk_normalize as _svc_bulk_normalize,
)
from colophon.services.editing import (
    bulk_remap as _svc_bulk_remap,
)
from colophon.services.editing import (
    bulk_set_field as _svc_bulk_set_field,
)
from colophon.services.encode import encode_book
from colophon.services.foster import (
    FosterResult,
    RestructureResult,
    derive_book_fields,
    foster_one,
    foster_work,
)
from colophon.services.ingest import ScanPlan, commit_scan, plan_scan, refresh_local, scan_ingest
from colophon.services.matching import gather_matches, query_for_book
from colophon.services.organize import organize_book
from colophon.services.tag_ops import (
    TagCommitResult,
    TagPlan,
    commit_tag,
    plan_tag,
    revert_tag_batch,
    tag_file,
)
from colophon.services.undo import undo_batch

logger = logging.getLogger(__name__)

_OP_ORGANIZE = "organize"  # audit-log op_type for a move into the library


class CoverSetResult(_Base):
    ok: bool = False
    error: str | None = None


def _detect_image_ext(data: bytes) -> str | None:
    """'.jpg' / '.png' from the leading magic bytes, or None if not a JPEG/PNG."""
    if data[:2] == b"\xff\xd8":
        return ".jpg"
    if data[:8] == b"\x89PNG\r\n\x1a\n":
        return ".png"
    return None


# Display labels for the metadata sources shown in the match-search dialog.
_SOURCE_LABELS = {
    "audnexus": "Audible",
    "openlibrary": "OpenLibrary",
    "googlebooks": "Google Books",
    "hardcover": "Hardcover",
    "internetarchive": "Internet Archive",
}


def _label_for(source: MetadataSource) -> str:
    """Human-facing label for a metadata source: its own `label` if set, else the
    known mapping, else a title-cased form of its name."""
    name = source.name
    return getattr(source, "label", None) or _SOURCE_LABELS.get(name, name.replace("_", " ").title())


class ChapterApplyResult(_Base):
    ok: bool = False
    count: int = 0
    audible_runtime_ms: int = 0
    source_runtime_ms: int = 0
    mismatch: bool = False
    error: str | None = None


class CatalogResult(_Base):
    affected_count: int = 0
    affected_ids: list[str] = []  # noqa: RUF012 - pydantic field default, copied per instance
    batch_id: str | None = None


class ProcessResult(_Base):
    book_id: str
    encoded: bool = False
    organized: bool = False
    detail: str | None = None


class DownloadEntry(_Base):
    key: str
    name: str
    status: str = "active"  # active / paused / done / failed
    detail: str = ""
    file_ids: list[int] | None = None  # chosen file subset (None = default audio+cover)


class EncodeJobOptions(_Base):
    encode: bool = True
    organize: bool = True
    delete_sources: bool = False
    concurrency: int = 2
    patterns: AudiobookPatterns | None = None  # per-run organize override; None = ctx.patterns


class BookProcessResult(_Base):
    book_id: str
    status: str = "queued"  # done / failed / cancelled / skipped
    detail: str | None = None


class EncodeJobResult(_Base):
    results: list[BookProcessResult] = []  # noqa: RUF012 - pydantic default, copied per instance


class AppController:
    def __init__(self, ctx: AppContext) -> None:
        self.ctx = ctx
        self._downloads: dict[str, DownloadEntry] = {}
        self._download_cancels: dict[str, CancelToken] = {}
        self._download_folders: dict[str, Path] = {}  # torrent id -> dest folder, so a resume reuses it

    def save_settings(self, config: Config) -> None:
        """Persist `config` and update the live context. The source list is rebuilt
        from the full available set (re-discovering abs-agg) and arranged per the
        saved order/disabled prefs, so reorders, enable/disable, and abs-agg URL
        changes all take effect without a restart. (db_path changes need a restart.)"""
        scheme_changed = (
            self.ctx.config.filename_template != config.filename_template
            or self.ctx.config.directory_scheme != config.directory_scheme
        )
        save_config(config, self.ctx.config_path)
        self.ctx.config = config
        self.ctx.sources = arrange_sources(
            build_all_sources(config),
            order=config.source_order,
            disabled=config.disabled_sources,
        )
        if scheme_changed:
            for book in self.ctx.books.list_all():
                if not book.phases:
                    ensure_phases(book)
                invalidate_from(book, Phase.CATEGORIZE)   # stale; next scan re-runs local phases
                self.ctx.books.upsert(book)

    # --- scanning / identification ---
    def scan_preview(
        self, roots: list[Path] | None = None,
        *, template: str | None = None, directory_scheme: str | None = None,
    ) -> ScanPlan:
        """Compute, without persisting, what a scan of `roots` (default: the configured
        scan paths) would do across all roots. `template`/`directory_scheme` override the
        saved defaults for this run (None = use config)."""
        roots = roots or self.ctx.config.scan_paths
        template = template if template is not None else self.ctx.config.filename_template
        directory_scheme = (
            directory_scheme if directory_scheme is not None else self.ctx.config.directory_scheme
        )
        combined = ScanPlan()
        for root in roots:
            plan = plan_scan(
                self.ctx.books, root, template=template, directory_scheme=directory_scheme,
            )
            combined.units.extend(plan.units)
            combined.new_books += plan.new_books
            combined.existing_books += plan.existing_books
            combined.fields_filled += plan.fields_filled
            combined.files_added += plan.files_added
        return combined

    def apply_scan(self, plan: ScanPlan) -> int:
        """Persist a previously-computed scan plan; returns the number written."""
        return commit_scan(self.ctx.books, plan)

    def scan(self, roots: list[Path] | None = None) -> int:
        """Convenience: preview then immediately commit. Returns the count."""
        return self.apply_scan(self.scan_preview(roots))

    def _root_for(self, book: BookUnit) -> Path:
        """The configured scan root that contains `book`, for re-running local phases."""
        for root in self.ctx.config.scan_paths:
            try:
                book.source_folder.relative_to(root)
                return root
            except ValueError:
                continue
        # Fallback: only reached for books outside every configured scan root.
        # Directory re-inference from source_folder.parent is best-effort in that case.
        return book.source_folder.parent

    def invalidate(self, book: BookUnit, from_phase: Phase) -> None:
        """Invalidate `from_phase` forward, auto-rerun the local phases, persist.
        Deferred phases are left stale for an explicit run."""
        if not book.phases:
            ensure_phases(book)
        invalidate_from(book, from_phase)
        refresh_local(
            book,
            root=self._root_for(book),
            template=self.ctx.config.filename_template,
            directory_scheme=self.ctx.config.directory_scheme,
        )
        self.ctx.books.upsert(book)

    def _hydrate(self, books: list[BookUnit]) -> list[BookUnit]:
        """Seed the phase map on legacy books that have an empty `phases` dict.
        Seeding is in-memory only; the next upsert of each book will persist it."""
        for b in books:
            if not b.phases:
                ensure_phases(b)
        return books

    def books_by_state(self, state: BookState) -> list[BookUnit]:
        """Books whose derived BookState equals `state` (legacy rows hydrated)."""
        return [b for b in self._hydrate(self.ctx.books.list_all()) if b.state is state]

    def books_with_phase(self, phase: Phase, status: PhaseState) -> list[BookUnit]:
        """Books whose `phase` is in `status` (legacy rows hydrated first)."""
        return [b for b in self._hydrate(self.ctx.books.list_all())
                if state_of(b, phase) is status]

    def phase_membership(self, books: list[BookUnit]) -> dict[Phase, list[BookUnit]]:
        """Group `books` by phase: each phase maps to the subset whose phase is FRESH.
        A book appears under every FRESH phase (cumulative funnel). One pass; pure over
        the supplied list (the caller applies any folder/scope filtering)."""
        out: dict[Phase, list[BookUnit]] = {p: [] for p in Phase}
        for book in books:
            for phase in Phase:
                if state_of(book, phase) is PhaseState.FRESH:
                    out[phase].append(book)
        return out

    def rerun_phase(self, books: list[BookUnit], phase: Phase) -> None:
        """Re-run `phase` for each book. Local phases (Search/Categorize/Identify)
        cascade-invalidate and auto-rerun via invalidate(). Deferred phases
        (Match/Tag/Organize/Encode) are not yet wired — their job dispatch is a
        follow-up — and raise NotImplementedError."""
        if phase not in LOCAL:
            raise NotImplementedError(f"re-run not yet wired for deferred phase {phase.value}")
        for book in books:
            self.invalidate(book, phase)

    # --- dashboard ---
    def dashboard_stats(self) -> dict[str, int]:
        books = self._hydrate(self.ctx.books.list_all())
        stats = {"total": len(books)}
        for state in BookState:
            stats[state.value] = sum(1 for b in books if b.state == state)
        return stats

    def get_book(self, book_id: str) -> BookUnit | None:
        book = self.ctx.books.get(book_id)
        return self._hydrate([book])[0] if book is not None else None

    async def book_cover(self, book_id: str, *, thumb: bool = False) -> tuple[bytes, str] | None:
        """A book's cover image as (bytes, mime): the cached file if present, else
        fetched from `cover_url` and cached for next time. With `thumb`, serve a
        small downscaled JPEG (for the list/navigator rows) instead of the
        full-size image, falling back to full if it can't be thumbnailed. None when
        the book has no cover or the fetch fails."""
        book = self.get_book(book_id)
        if book is None:
            return None
        source = None
        if book.cover_path and book.cover_path.exists():
            source = book.cover_path
        elif book.cover_url:
            source = await ensure_cached_cover(book, dest_dir=book.source_folder)
            if source is not None:
                self.ctx.books.upsert(book)  # remember the cache location
        if source is None:
            return None
        if thumb:
            tn = await asyncio.to_thread(thumbnail_bytes, source)
            if tn is not None:
                return tn
        return source.read_bytes(), mime_for_suffix(source)
    async def ensure_cover_cached(self, book: BookUnit) -> None:
        """Cache the book's cover_url into cover_path (if not already cached) so a
        synchronous encode can embed it. No-op when a cached cover exists or there
        is no cover_url."""
        if book.cover_path and book.cover_path.exists():
            return
        path = await ensure_cached_cover(book, dest_dir=book.source_folder)
        if path is not None:
            self.ctx.books.upsert(book)

    def set_cover_url(self, book: BookUnit, url: str) -> None:
        """Point the book at a new cover URL, clearing any cached file so the new
        image is fetched + served on demand."""
        book.cover_url = (url or "").strip() or None
        book.cover_path = None
        book.touch()
        self.ctx.books.upsert(book)
        self._sync_sidecar(book)

    def set_cover_upload(
        self, book: BookUnit, data: bytes, filename: str | None = None
    ) -> CoverSetResult:
        """Write an uploaded JPEG/PNG to the book's folder as cover.<ext> and use
        it (clearing cover_url). Rejects non-image bytes."""
        ext = _detect_image_ext(data)
        if ext is None:
            return CoverSetResult(ok=False, error="Not a JPEG or PNG image")
        path = book.source_folder / f"cover{ext}"
        try:
            book.source_folder.mkdir(parents=True, exist_ok=True)
            path.write_bytes(data)
        except OSError as e:
            logger.warning(f"writing cover to {path} failed: {e}")
            return CoverSetResult(ok=False, error="Could not write cover file")
        book.cover_path = path
        book.cover_url = None
        book.touch()
        self.ctx.books.upsert(book)
        self._sync_sidecar(book)
        return CoverSetResult(ok=True)

    def set_abridged(self, book: BookUnit, value: bool | None) -> None:
        """Set the abridged flag directly (None = unknown) and persist. Not part of
        the undoable field history."""
        book.abridged = value
        book.touch()
        self.ctx.books.upsert(book)
        self._sync_sidecar(book)

    async def cover_candidates(self, book: BookUnit) -> list[str]:
        """Distinct cover URLs from a match search, best-ranked first."""
        out: list[str] = []
        seen: set[str] = set()
        for r in await self.get_matches(book):
            if r.cover_url and r.cover_url not in seen:
                seen.add(r.cover_url)
                out.append(r.cover_url)
        return out

    def clear_cover(self, book: BookUnit) -> None:
        """Remove the book's cover (both URL and cached file reference)."""
        book.cover_url = None
        book.cover_path = None
        book.touch()
        self.ctx.books.upsert(book)
        self._sync_sidecar(book)

    def books_all(self) -> list[BookUnit]:
        """All persisted books (used by callers that need the full set)."""
        return self.ctx.books.list_all()

    def _distinct(self, project: Callable[[BookUnit], Iterable[str]]) -> list[str]:
        """Sorted distinct values projected from every book (editor autocomplete)."""
        return sorted({v for b in self.ctx.books.list_all() for v in project(b)})

    def known_authors(self) -> list[str]:
        """Distinct author names across the library, sorted (editor autocomplete)."""
        return self._distinct(lambda b: b.authors)

    def known_series(self) -> list[str]:
        """Distinct series names across the library, sorted (editor autocomplete)."""
        return self._distinct(lambda b: (s.name for s in b.series))

    def genre_policy(self) -> GenrePolicy:
        """Build the active genre policy from config."""
        return GenrePolicy(
            mapping=self.ctx.config.genre_mapping,
            accepted=self.ctx.config.accepted_genres,
            whitelist_enabled=self.ctx.config.genre_whitelist_enabled,
        )

    def known_genres(self) -> list[str]:
        """Distinct genre names across the library, sorted (editor autocomplete)."""
        return self._distinct(lambda b: b.genres)

    def catalog_entries(self, kind: str) -> list[CatalogEntry]:
        """Distinct values of `kind` across the whole library, with usage counts."""
        return list_entries(self.ctx.books.list_all(), kind)

    def _catalog_apply(self, kind: str, mapping: dict[str, str | None]) -> CatalogResult:
        affected, batch_id = apply_catalog_mapping(self.ctx.books, self.ctx.history, kind, mapping)
        for book_id in affected:
            book = self.ctx.books.get(book_id)
            if book is not None:
                self._sync_sidecar(book)
        return CatalogResult(affected_count=len(affected), affected_ids=affected, batch_id=batch_id)

    def rename_catalog_entry(self, kind: str, old: str, new: str) -> CatalogResult:
        return self._catalog_apply(kind, {old: new})

    def merge_catalog_entries(self, kind: str, sources: list[str], target: str) -> CatalogResult:
        return self._catalog_apply(kind, {s: target for s in sources})

    def delete_catalog_entry(self, kind: str, name: str) -> CatalogResult:
        return self._catalog_apply(kind, {name: None})

    def known_tags(self) -> list[str]:
        """Distinct tag names across the library, sorted (editor autocomplete)."""
        return self._distinct(lambda b: b.tags)

    # --- workspace navigator ---
    def library_tree(self) -> LibraryTree:
        """Group all books into Author -> Series/standalone, plus a needs-id list."""
        return build_library_tree(self._hydrate(self.ctx.books.list_all()))

    def list_directory(self, path: Path) -> DirectoryListing:
        """List a directory's immediate children: subdirs first, then files.

        Returns an empty listing if the path is absent or not a directory."""
        if not path.is_dir():
            return DirectoryListing(path=path, entries=[])
        try:
            children = list(path.iterdir())
        except OSError:
            return DirectoryListing(path=path, entries=[])
        dirs = sorted((c for c in children if c.is_dir()), key=lambda c: c.name.casefold())
        files = sorted((c for c in children if c.is_file()), key=lambda c: c.name.casefold())
        entries = [DirEntry(path=c, name=c.name, is_dir=True, is_audio=False) for c in dirs]
        entries += [
            DirEntry(path=c, name=c.name, is_dir=False, is_audio=is_audio_file(c)) for c in files
        ]
        return DirectoryListing(path=path, entries=entries)

    # --- editing / undo ---
    def _sync_sidecar(self, book: BookUnit) -> None:
        """Best-effort: mirror a book's saved metadata into its source sidecar.

        Catches broadly on purpose: this is a non-critical side effect that must
        never propagate and lose the already-persisted DB edit.
        """
        try:
            write_sidecar(book.source_folder, book)
        except Exception as e:  # broad on purpose: best-effort side effect, must not lose the DB edit
            logger.warning(f"sidecar write failed for {book.id}: {e}")

    def edit_field(self, book: BookUnit, field: str, value: str | None) -> str:
        batch = set_field_value(self.ctx.books, self.ctx.history, book, field, value)
        self._sync_sidecar(book)
        self.invalidate(book, Phase.TAG)
        return batch

    def save_fields(self, book: BookUnit, updates: dict[str, str | None]) -> str:
        """Apply manual metadata edits to `book` in one batch and re-sync its
        sidecar. Returns the batch id (undoable via undo)."""
        batch = apply_fields(
            self.ctx.books, self.ctx.history, book, updates, provenance=Provenance.MANUAL.value
        )
        self._sync_sidecar(book)
        self.invalidate(book, Phase.TAG)
        return batch

    # --- Real-Debrid acquisition (issue #11) ---
    def rd_configured(self) -> bool:
        return bool(self.ctx.config.real_debrid_token)

    def rd_client(self) -> RealDebridClient:
        token = self.ctx.config.real_debrid_token
        if not token:
            raise ValueError("no Real-Debrid token configured")
        return RealDebridClient(token)

    def _rd_download_dir(self) -> Path:
        return self.ctx.config.real_debrid_download_dir or (default_db_path().parent / "downloads")

    async def rd_test_connection(self, token: str | None = None) -> RdUser:
        """Verify the RD token by fetching the account. Tests `token` if given
        (without persisting it), else the configured one. Lets the Settings page
        validate a typed-but-unsaved token without mutating live config."""
        token = token or self.ctx.config.real_debrid_token
        if not token:
            raise ValueError("no Real-Debrid token configured")
        client = RealDebridClient(token)
        try:
            return await client.user()
        finally:
            await client.aclose()

    async def rd_list_candidates(self) -> list[AcquireCandidate]:
        client = self.rd_client()
        try:
            return await list_candidates(client)
        finally:
            await client.aclose()

    async def rd_add_magnet(self, magnet: str) -> str:
        """Add a magnet to Real-Debrid and select its files. Returns the torrent id."""
        client = self.rd_client()
        try:
            return await add_torrent(client, magnet)
        finally:
            await client.aclose()

    def active_downloads(self) -> list[DownloadEntry]:
        """Every tracked download (active / paused / done / failed)."""
        return list(self._downloads.values())

    def clear_finished_downloads(self) -> None:
        """Drop the done/failed entries (and their cancel tokens) from the registry."""
        for key in [k for k, e in self._downloads.items() if e.status in ("done", "failed")]:
            self._downloads.pop(key, None)
            self._download_cancels.pop(key, None)
            self._download_folders.pop(key, None)

    def cancel_download(self, key: str) -> None:
        """Signal a cancel for the in-flight download `key` (no-op if unknown)."""
        token = self._download_cancels.get(key)
        if token is not None:
            token.cancel()

    async def _run_download(
        self, torrent_id: str, name: str,
        *, file_ids: list[int] | None = None,
        progress: Callable[[int, int, str], None] | None = None,
    ) -> tuple[AcquireResult, list[str]]:
        """Download one torrent and ingest its folder, tracking progress/status in
        the registry. A cancel leaves the entry 'paused' (its .part files retained
        for resume); otherwise it ends 'done' or 'failed'. `file_ids` restricts the
        download to a chosen file subset (stored on the entry so a resume re-applies
        it); None keeps the default audio+cover set."""
        entry = DownloadEntry(key=torrent_id, name=name, status="active", file_ids=file_ids)
        self._downloads[torrent_id] = entry
        token = CancelToken()
        self._download_cancels[torrent_id] = token

        def _byte_progress(done: int, total: int) -> None:
            entry.detail = f"{done * 100 // total}%" if total else f"{done} bytes"

        client = self.rd_client()
        try:
            info = await client.torrent_info(torrent_id)
            result = await download_torrent(
                client, info, self._rd_download_dir(),
                folder=self._download_folders.get(torrent_id),
                file_ids=set(file_ids) if file_ids is not None else None,
                progress=progress, byte_progress=_byte_progress, cancel=token,
            )
        finally:
            await client.aclose()
        # remember the folder so a later resume reuses it (and finds the retained .part)
        self._download_folders[torrent_id] = result.folder

        cancelled = any((f.error == "cancelled") for f in result.files)
        book_ids: list[str] = []
        if result.any_ok:
            books = scan_ingest(
                self.ctx.books, result.folder,
                template=self.ctx.config.filename_template,
                directory_scheme=self.ctx.config.directory_scheme,
            )
            book_ids = [b.id for b in books]
        entry.status = "paused" if cancelled else ("done" if result.any_ok else "failed")
        return result, book_ids

    async def rd_download(
        self, torrent_id: str, *, name: str | None = None,
        file_ids: list[int] | None = None,
        progress: Callable[[int, int, str], None] | None = None,
    ) -> tuple[AcquireResult, list[str]]:
        """Download a torrent (optionally only `file_ids`), then ingest the folder,
        tracked in the registry. Returns the download result and the ids of any newly
        registered books. `name` is the display label for the Downloads section
        (defaults to the torrent id); `file_ids=None` keeps the default audio+cover
        set; `progress(idx, total, filename)` is the existing per-file callback."""
        return await self._run_download(
            torrent_id, name or torrent_id, file_ids=file_ids, progress=progress
        )

    async def resume_download(self, key: str) -> tuple[AcquireResult, list[str]]:
        """Re-run a tracked (e.g. paused) download from its retained .part files,
        re-applying the file subset it was started with."""
        entry = self._downloads.get(key)
        name = entry.name if entry else key
        file_ids = entry.file_ids if entry else None
        return await self._run_download(key, name, file_ids=file_ids)

    def _rd_download_dir_in_scan_paths(self) -> bool:
        return self._rd_download_dir() in self.ctx.config.scan_paths

    def should_prompt_downloads_scan(self) -> bool:
        """Whether to offer adding the downloads dir to the scan paths: only when it
        isn't already a scan path and the prompt hasn't been dismissed before."""
        return not self.ctx.config.downloads_scan_prompt_seen and not self._rd_download_dir_in_scan_paths()

    def add_downloads_to_scan_paths(self) -> None:
        """Add the downloads dir to the scan paths (deduped), dismiss the prompt, and persist."""
        cfg = self.ctx.config
        d = self._rd_download_dir()
        if d not in cfg.scan_paths:
            cfg.scan_paths = [*cfg.scan_paths, d]
        cfg.downloads_scan_prompt_seen = True
        self.save_settings(cfg)

    def mark_downloads_scan_prompt_seen(self) -> None:
        """Dismiss the downloads-scan prompt (without adding the dir) and persist."""
        cfg = self.ctx.config
        cfg.downloads_scan_prompt_seen = True
        self.save_settings(cfg)

    # --- filename parsing (interactive, FR-1.x) ---
    def book_filename(self, book: BookUnit) -> str:
        """The name used for template parsing: the first source file's filename,
        falling back to the source folder's name when no files are recorded."""
        if book.source_files:
            return book.source_files[0].path.name
        return book.source_folder.name

    def preview_filename_parse(self, book: BookUnit, template: str) -> dict[str, str]:
        """Parse `book`'s filename with `template`. Raises ValueError when the
        template is malformed; returns {} when the filename does not match."""
        pattern = compile_template(template)
        return parse_filename(pattern, self.book_filename(book)) or {}

    def filename_parse_updates(
        self, book: BookUnit, template: str, fields: set[str]
    ) -> dict[str, str]:
        """The field updates applying `template` to `book` would actually make,
        limited to `fields`: parsed, non-empty values, with `sequence` dropped
        unless the book will have a series (set_field no-ops a sequence with no
        series name). Ordered so series precedes sequence. The single source of
        truth shared by the parse preview and apply, so they cannot drift."""
        parsed = self.preview_filename_parse(book, template)
        updates = {k: v for k, v in parsed.items() if k in fields and v}
        if "sequence" in updates and "series" not in updates and not book.series:
            del updates["sequence"]
        return dict(sorted(updates.items(), key=lambda kv: kv[0] != "series"))

    def apply_filename_parse(
        self, books: list[BookUnit], template: str, fields: set[str]
    ) -> int:
        """Parse each book's filename with `template` and write the chosen `fields`
        under "filename" provenance. Each book's change is its own undoable batch.
        Returns the number of books that received at least one real field change.
        Raises ValueError on a malformed template (validated before any writes)."""
        compile_template(template)  # validate up front; raises on bad placeholders
        changed = 0
        for book in books:
            updates = self.filename_parse_updates(book, template, fields)
            if not updates:
                continue
            apply_fields(
                self.ctx.books, self.ctx.history, book, updates,
                provenance=Provenance.FILENAME.value,
            )
            self._sync_sidecar(book)
            changed += 1
        return changed

    def _push_history(self, items: list, value: object) -> None:
        """Move `value` to the front (dedup), cap the list, and persist the config."""
        if value in items:
            items.remove(value)
        items.insert(0, value)
        del items[PATTERN_HISTORY_CAP:]
        save_config(self.ctx.config, self.ctx.config_path)

    def _remove_history(self, items: list, value: object) -> None:
        if value in items:
            items.remove(value)
            save_config(self.ctx.config, self.ctx.config_path)

    def record_filename_template(self, template: str) -> None:
        """Record a used filename template at the front of the capped history."""
        t = template.strip()
        if t:
            self._push_history(self.ctx.config.recent_filename_templates, t)

    def record_directory_scheme(self, scheme: str) -> None:
        """Record a used directory scheme at the front of the capped history."""
        s = scheme.strip()
        if s:
            self._push_history(self.ctx.config.recent_directory_schemes, s)

    def record_organize_pattern(self, folder: str, file: str) -> None:
        """Record a used organize folder+file pair at the front of the capped history."""
        folder, file = folder.strip(), file.strip()
        if folder or file:
            self._push_history(
                self.ctx.config.recent_organize_patterns, OrganizePattern(folder=folder, file=file)
            )

    def remove_filename_template(self, template: str) -> None:
        self._remove_history(self.ctx.config.recent_filename_templates, template)

    def remove_directory_scheme(self, scheme: str) -> None:
        self._remove_history(self.ctx.config.recent_directory_schemes, scheme)

    def remove_organize_pattern(self, folder: str, file: str) -> None:
        self._remove_history(
            self.ctx.config.recent_organize_patterns, OrganizePattern(folder=folder, file=file)
        )

    def clear_pattern_history(self) -> None:
        """Empty all three pattern histories and persist."""
        self.ctx.config.recent_filename_templates.clear()
        self.ctx.config.recent_directory_schemes.clear()
        self.ctx.config.recent_organize_patterns.clear()
        save_config(self.ctx.config, self.ctx.config_path)

    def move_file(self, book: BookUnit, path: Path, delta: int) -> None:
        """Move a file up (delta=-1) or down (delta=+1) in the book's order."""
        paths = [sf.path for sf in book.source_files]
        if path not in paths:
            return
        i = paths.index(path)
        j = max(0, min(len(paths) - 1, i + delta))
        if i == j:
            return
        paths[i], paths[j] = paths[j], paths[i]
        file_ops.reorder(book, paths)
        book.touch()
        self.ctx.books.upsert(book)

    def exclude_file(self, book: BookUnit, path: Path) -> None:
        """Remove a file from the book's source list (does not delete it from disk)."""
        file_ops.exclude(book, path)
        book.touch()
        self.ctx.books.upsert(book)

    def rename_file(self, book: BookUnit, path: Path, new_name: str) -> Path | None:
        """Rename a file on disk; returns the new path, or None on collision/error."""
        try:
            new = file_ops.rename(book, path, new_name)
        except (OSError, ValueError) as e:
            logger.warning(f"rename failed for {path}: {e}")
            return None
        book.touch()
        self.ctx.books.upsert(book)
        return new

    def foster_files(self, paths: list[Path]) -> list[FosterResult]:
        """Move each loose file into its own stem-named subdirectory, then
        re-scan affected parents so the new single-file books register.

        Re-scanning a parent updates its book to the remaining loose files (or,
        if none remain, leaves a stale book that `scan_ingest` won't touch -- so
        we prune any parent folder that no longer directly contains audio).
        Returns one FosterResult per input path; a failure on one file does not
        abort the batch.
        """
        results: list[FosterResult] = []
        parents: set[Path] = set()
        for path in paths:
            try:
                destination = foster_one(path)
            except (OSError, ValueError) as e:
                logger.warning(f"foster failed for {path}: {e}")
                results.append(FosterResult(source=path, ok=False, error=str(e)))
                continue
            results.append(FosterResult(source=path, destination=destination, ok=True))
            parents.add(path.parent)

        template = self.ctx.config.filename_template
        for parent in parents:
            # scan_ingest walks the parent's full subtree (os.walk), so this both
            # registers the new child book(s) and refreshes the parent's own book.
            # Inference depth is measured from `parent` here (not the configured
            # scan root), so a multi-segment directory_scheme generally won't match
            # on a foster re-scan; a later full scan re-derives it. This only ever
            # yields fewer inferences, never wrong ones (inference is weak evidence).
            scan_ingest(
                self.ctx.books,
                parent,
                template=template,
                directory_scheme=self.ctx.config.directory_scheme,
            )
            if not self._has_direct_audio(parent):
                self.ctx.books.delete(BookUnit.id_for(parent))
        return results

    @staticmethod
    def _has_direct_audio(folder: Path) -> bool:
        """True if `folder` directly contains at least one audio file."""
        try:
            return any(is_audio_file(c) for c in folder.iterdir() if c.is_file())
        except OSError:
            return False

    _SEVERITY_RANK: ClassVar[dict[FindingSeverity, int]] = {
        FindingSeverity.ERROR: 0,
        FindingSeverity.WARN: 1,
        FindingSeverity.INFO: 2,
    }

    def _active_findings(self, book: BookUnit) -> list[Finding]:
        """Findings not dismissed via acknowledge."""
        return [f for f in book.findings if f.code not in book.acknowledged_findings]

    def books_needing_attention(self) -> list[BookUnit]:
        """All books carrying at least one un-acknowledged finding, most severe first."""
        flagged = [b for b in self.ctx.books.list_all() if self._active_findings(b)]
        return sorted(
            flagged,
            key=lambda b: min(self._SEVERITY_RANK[f.severity] for f in self._active_findings(b)),
        )

    def acknowledge_finding(self, book: BookUnit, code: FindingCode) -> None:
        """Dismiss an advisory finding so a re-scan won't resurface it."""
        if code not in book.acknowledged_findings:
            book.acknowledged_findings = [*book.acknowledged_findings, code]
            book.touch()
            self.ctx.books.upsert(book)

    def split_into_works(self, book: BookUnit) -> RestructureResult:
        """Foster each detected work in `book` into its own subfolder, then
        re-scan the parent so the new books register (mirrors foster_files)."""
        result = RestructureResult()
        parent = book.source_folder
        for work in book.detected_works:
            try:
                foster_work(work.files, parent, work.label)
                result.fostered += len(work.files)
            except OSError as e:
                logger.warning(f"split failed for {work.label} in {parent}: {e}")
                result.failures.append(
                    FosterResult(source=parent / work.label, ok=False, error=str(e))
                )
        # Re-scan the parent so the new subfolder books register and the parent
        # book refreshes; prune the parent if no audio remains directly in it
        # (same re-scan-from-parent pattern as foster_files).
        template = self.ctx.config.filename_template
        scan_ingest(
            self.ctx.books, parent, template=template,
            directory_scheme=self.ctx.config.directory_scheme,
        )
        if not self._has_direct_audio(parent):
            self.ctx.books.delete(BookUnit.id_for(parent))
        return result

    def _restructure_sync(
        self, paths: list[Path], author_override: str | None
    ) -> tuple[list[FosterResult], list[BookUnit]]:
        """Foster `paths`, then set author/title on each new book. Returns the
        foster results and the new books. Blocking; call via asyncio.to_thread."""
        results = self.foster_files(paths)
        new_books: list[BookUnit] = []
        for r in results:
            if not r.ok or r.destination is None:
                continue
            book_id = BookUnit.id_for(r.destination.parent)
            book = self.ctx.books.get(book_id)
            if book is None:
                logger.warning(f"restructure: no book found at {r.destination.parent}")
                continue
            author, title = derive_book_fields(r.destination, author_override)
            self.save_fields(book, {"author": author, "title": title})
            refreshed = self.ctx.books.get(book_id)
            if refreshed is not None:
                new_books.append(refreshed)
        return results, new_books

    async def restructure_as_books(
        self,
        paths: list[Path],
        *,
        author_override: str | None = None,
        write_tags: bool = False,
    ) -> RestructureResult:
        """Foster each file into its own book directory, set author (from the
        file's containing folder, or `author_override`) and a normalized title,
        and optionally write the corrected tags. The blocking disk work runs in a
        worker thread; the optional retag is awaited."""
        results, new_books = await asyncio.to_thread(
            self._restructure_sync, paths, author_override
        )
        retagged = 0
        if write_tags and new_books:
            tag_results = await self.write_tags_books(new_books)
            retagged = sum(t.written for t in tag_results)
        return RestructureResult(
            fostered=sum(1 for r in results if r.ok),
            retagged=retagged,
            failures=[r for r in results if not r.ok],
            book_ids=[b.id for b in new_books],
        )

    def tag_plan(self, book: BookUnit) -> TagPlan:
        """The dry-run preview of writing this book's metadata into its files."""
        return plan_tag(book)

    async def write_tags(self, book: BookUnit) -> TagCommitResult:
        """Write tags into one book's files. See write_tags_books."""
        (result,) = await self.write_tags_books([book])
        return result

    async def write_tags_books(
        self, books: list[BookUnit],
        progress: Callable[[int, BookUnit, TagCommitResult], None] | None = None,
    ) -> list[TagCommitResult]:
        """Fetch+cache each book's cover (best effort), then write tags into its
        files on a worker thread, logging every write for recovery. All books share
        one batch id, so a single undo reverts the whole selection. `progress`, when
        given, is called after each book as (done_count, book, result) so the UI can
        show per-book status."""
        batch_id = new_batch_id()
        results: list[TagCommitResult] = []
        for book in books:
            await ensure_cached_cover(book, dest_dir=book.source_folder)
            self.ctx.books.upsert(book)
            result = await asyncio.to_thread(
                commit_tag, book, operations=self.ctx.operations, batch_id=batch_id
            )
            results.append(result)
            if progress is not None:
                progress(len(results), book, result)
        return results

    def undo_tag_batch(self) -> bool:
        """Revert the most recent tag batch. Returns False if there is none."""
        batch_id = self.ctx.operations.latest_batch_id()
        if batch_id is None:
            return False
        revert_tag_batch(self.ctx.operations, batch_id)
        return True

    def remap(self, book: BookUnit, *, src: str, dst: str, clear_source: bool) -> str:
        batch = remap_field(self.ctx.books, self.ctx.history, book, src=src, dst=dst, clear_source=clear_source)
        self._sync_sidecar(book)
        return batch

    def bulk_edit(self, books: list[BookUnit], field: str, value: str | None) -> str:
        batch = _svc_bulk_set_field(self.ctx.books, self.ctx.history, books, field, value)
        for book in books:
            self._sync_sidecar(book)
        for book in books:
            self.invalidate(book, Phase.TAG)
        return batch

    def bulk_normalize(self, books: list[BookUnit], fields: list[str]) -> str:
        """Normalize the given text `fields` across `books` in one undoable batch."""
        batch = _svc_bulk_normalize(
            self.ctx.books, self.ctx.history, books, fields, genre_policy=self.genre_policy()
        )
        for book in books:
            self._sync_sidecar(book)
        for book in books:
            self.invalidate(book, Phase.TAG)
        return batch

    def bulk_remap(self, books: list[BookUnit], *, src: str, dst: str, clear_source: bool) -> str:
        batch = _svc_bulk_remap(self.ctx.books, self.ctx.history, books, src=src, dst=dst, clear_source=clear_source)
        for book in books:
            self._sync_sidecar(book)
        for book in books:
            self.invalidate(book, Phase.TAG)
        return batch

    def batch_changes(self, batch_id: str) -> list[EditChange]:
        """Return the recorded changes for `batch_id` (empty if none)."""
        return self.ctx.history.list_batch(batch_id)

    def undo(self, batch_id: str) -> None:
        affected_ids = {c.book_id for c in self.ctx.history.list_batch(batch_id)}
        undo_batch(self.ctx.books, self.ctx.history, batch_id)
        for book_id in affected_ids:
            restored = self.ctx.books.get(book_id)
            if restored is not None:
                self._sync_sidecar(restored)

    def undo_last(self) -> bool:
        batch_id = self.ctx.history.latest_batch_id()
        if batch_id is None:
            return False
        self.undo(batch_id)
        return True

    def mark_ready(self, book: BookUnit) -> None:
        book.manually_confirmed = True
        mark(book, Phase.IDENTIFY, PhaseState.FRESH)
        resync_state(book, ready_threshold=self.ctx.config.review_threshold)
        book.touch()
        self.ctx.books.upsert(book)

    def confirm_confidence(self, book: BookUnit) -> None:
        """Manually confirm a book: force confidence to 100, mark it Ready, and
        flag it as manual so the badge/recheck know it was set by hand."""
        book.confidence = 100.0
        book.confidence_signals = [
            ConfidenceSignal(name="manual_confirmation", points=100, detail="Manually confirmed")
        ]
        book.manually_confirmed = True
        mark(book, Phase.IDENTIFY, PhaseState.FRESH)
        resync_state(book, ready_threshold=self.ctx.config.review_threshold)
        book.touch()
        self.ctx.books.upsert(book)

    async def recheck_confidence(self, book: BookUnit) -> None:
        """Revert to auto confidence: re-query all sources, rescore, clear the
        manual flag, and persist."""
        results = await gather_matches(self.ctx.sources, query_for_book(book))
        self._rescore_after_match(book, results)
        book.touch()
        self.ctx.books.upsert(book)

    # --- match review / apply (FR-2.4, FR-3.3) ---
    async def get_matches(self, book: BookUnit) -> list[SourceResult]:
        """Re-query all sources for `book` and return candidate matches, best first."""
        results = await gather_matches(self.ctx.sources, query_for_book(book))
        return self._score(book, results).ranked

    def identify_candidates(self) -> list[BookUnit]:
        """Books eligible for Identify: not manually confirmed and not organized."""
        return [
            b for b in self.ctx.books.list_all()
            if not b.manually_confirmed and b.output_path is None
        ]

    async def identify_preview(
        self, *, progress: Callable[[str, str], None] | None = None
    ) -> IdentifyPlan:
        """Query all sources for every candidate and partition by the review threshold,
        without persisting anything. `progress(book_id, kind)` streams per-book outcomes."""
        candidates = self.identify_candidates()
        skipped = len(self.ctx.books.list_all()) - len(candidates)
        source_names = [s.name for s in self.ctx.sources]
        proposals = await self.quick_match_scan(candidates, source_names, progress=progress)
        return self._identify_plan(proposals, skipped)

    def _identify_plan(self, proposals: list[QuickMatchProposal], skipped: int) -> IdentifyPlan:
        """Partition scanned proposals into the IdentifyPlan counts (shared by preview/retry)."""
        threshold = self.ctx.config.review_threshold
        to_apply = sum(1 for p in proposals if p.best is not None and p.confidence >= threshold)
        return IdentifyPlan(
            proposals=proposals, threshold=threshold,
            to_apply=to_apply, to_review=len(proposals) - to_apply, skipped=skipped,
        )

    async def retry_identify(
        self, plan: IdentifyPlan, book_ids: list[str],
        *, progress: Callable[[str, str], None] | None = None,
    ) -> IdentifyPlan:
        """Re-query the books in `book_ids`, replace their proposals in `plan`, and recompute
        the partition. Books not in `book_ids` keep their existing proposals."""
        wanted = set(book_ids)
        targets = [p.book for p in plan.proposals if p.book.id in wanted]
        source_names = [s.name for s in self.ctx.sources]
        fresh = await self.quick_match_scan(targets, source_names, progress=progress)
        fresh_by_id = {p.book.id: p for p in fresh}
        merged = [fresh_by_id.get(p.book.id, p) for p in plan.proposals]
        return self._identify_plan(merged, plan.skipped)

    def apply_identify(self, plan: IdentifyPlan) -> IdentifySummary:
        """Fill-empty apply the confident proposals (Ready) and re-score the rest
        (Needs review), in one undo batch. Manually-confirmed and organized books
        are not in the plan and are never touched."""
        items: list[tuple[BookUnit, dict[str, str | None], str]] = []
        for p in plan.proposals:
            if p.best is not None and p.confidence >= plan.threshold:
                updates = {
                    k: v for k, v in self.match_field_values(p.best).items()
                    if not get_field(p.book, k)
                }
                self._normalize_match_updates(updates)
                self._capture_match_signals(p.book, p.best, fill_empty=True)
                items.append((p.book, updates, p.best.provider))
        batch = bulk_apply_fields(self.ctx.books, self.ctx.history, items) if items else ""

        auto = 0
        for p in plan.proposals:
            ready = self._rescore_and_persist(p)
            if ready and p.best is not None and p.confidence >= plan.threshold:
                auto += 1
        return IdentifySummary(
            auto_matched=auto, routed_to_review=len(plan.proposals) - auto, batch_id=batch,
        )

    async def quick_match_scan(
        self,
        books: list[BookUnit],
        source_names: list[str],
        search_fields: set[str] | None = None,
        *,
        progress: Callable[[str, str], None] | None = None,
    ) -> list[QuickMatchProposal]:
        """For each book, query the chosen sources, score the candidates, and
        return a proposal carrying the best result, all gathered results (for
        later re-scoring), and the scan confidence. Books are scanned concurrently.
        `search_fields` (when given) restricts which fields seed the query.
        `progress(book_id, kind)` fires once per book as it resolves, kind 'ok' when a
        source returned a candidate else 'fail'."""
        chosen = [s for s in self.ctx.sources if s.name in source_names]

        async def _scan(book: BookUnit) -> QuickMatchProposal:
            results = await gather_matches(chosen, query_for_book(book, search_fields))
            outcome = self._score(book, results)
            if progress is not None:
                progress(book.id, "ok" if outcome.best is not None else "fail")
            return QuickMatchProposal(
                book=book, best=outcome.best, results=results, confidence=outcome.confidence
            )

        return list(await asyncio.gather(*(_scan(b) for b in books)))

    def quick_match_apply(self, proposals: list[QuickMatchProposal]) -> QuickMatchSummary:
        """Apply the best result of each proposal (overwrite all present fields,
        capture cover) in one undoable batch, then re-score each updated book with
        its carried results and set confidence/state. Proposals without a best
        result are skipped. Returns a summary (applied, now_ready, batch_id)."""
        applicable = [p for p in proposals if p.best is not None]
        if not applicable:
            return QuickMatchSummary()

        items: list[tuple[BookUnit, dict[str, str | None], str]] = []
        for p in applicable:
            updates = self.match_field_values(p.best)
            self._merge_genre_tag_updates(p.book, p.best, updates)
            self._normalize_match_updates(updates)
            self._capture_match_signals(p.book, p.best, fill_empty=False)
            items.append((p.book, updates, p.best.provider))

        batch = bulk_apply_fields(self.ctx.books, self.ctx.history, items)

        now_ready = sum(self._rescore_and_persist(p) for p in applicable)

        return QuickMatchSummary(
            applied_count=len(applicable), now_ready_count=now_ready, batch_id=batch
        )

    def _capture_match_signals(self, book: BookUnit, best: SourceResult, *, fill_empty: bool) -> None:
        """Capture the non-field match signals (cover URL, abridged) onto `book`.
        With `fill_empty`, only set a value the book is missing (Identify's
        non-destructive semantics); otherwise overwrite (Quick Match)."""
        if best.cover_url and (not fill_empty or (not book.cover_path and not book.cover_url)):
            book.cover_url = best.cover_url
        if best.abridged is not None and (not fill_empty or book.abridged is None):
            book.abridged = best.abridged

    def _rescore_and_persist(self, proposal: QuickMatchProposal) -> bool:
        """Re-score a proposal's book against its carried results, then persist the
        book and sync its sidecar. Returns whether the book is now Ready."""
        ready = self._rescore_after_match(proposal.book, proposal.results)
        proposal.book.touch()
        self.ctx.books.upsert(proposal.book)
        self._sync_sidecar(proposal.book)
        return ready

    def _rescore_after_match(self, book: BookUnit, results: list[SourceResult]) -> bool:
        """Re-score `book` against `results` and set its confidence, signals, and
        state (Ready when confident and it has an identity). Returns True if it is
        now Ready. Confidence/state are persisted by the caller (not part of the
        undoable field batch)."""
        outcome = self._score(book, results)
        book.confidence = outcome.confidence
        book.confidence_signals = outcome.signals
        book.manually_confirmed = False
        has_identity = bool(book.authors) or bool(book.series)
        ready = outcome.confidence >= self.ctx.config.review_threshold and has_identity
        mark(book, Phase.IDENTIFY, PhaseState.FRESH)
        resync_state(book, ready_threshold=self.ctx.config.review_threshold)
        return ready

    def _authority_map(self) -> dict[str, int]:
        """provider name -> authority rank (0 = most authoritative), from the
        enabled sources in their arranged order."""
        return {s.name: i for i, s in enumerate(self.ctx.sources)}

    def _score(self, book: BookUnit, results: list[SourceResult]) -> IdentificationOutcome:
        return score_identification(book, results, authority=self._authority_map())

    def available_sources(self) -> list[tuple[str, str]]:
        """The configured metadata sources as (name, display label), in priority
        order, so the search dialog can list exactly the available services."""
        return [(s.name, _label_for(s)) for s in self.ctx.sources]

    def source_settings(self) -> list[tuple[str, str, bool]]:
        """(name, label, enabled) for every currently-known source: enabled ones in
        authority order first, then known-but-disabled providers. Re-discovers
        abs-agg so newly-available providers appear and stale ones drop."""
        enabled_names = {s.name for s in self.ctx.sources}
        rows = [(s.name, _label_for(s), True) for s in self.ctx.sources]
        rows += [
            (s.name, _label_for(s), False)
            for s in build_all_sources(self.ctx.config)
            if s.name not in enabled_names
        ]
        return rows

    def source_label(self, name: str) -> str:
        """Human-facing label for a source/provenance name (e.g. 'audnexus' -> 'Audible')."""
        for s in self.ctx.sources:
            if s.name == name:
                return _label_for(s)
        return _SOURCE_LABELS.get(name, name.replace("_", " ").title())

    def review_threshold(self) -> float:
        """The confidence threshold above which a match is auto-checked / a book is Ready."""
        return self.ctx.config.review_threshold

    async def search_matches(
        self,
        book: BookUnit,
        *,
        title: str | None,
        author: str | None,
        series: str | None,
        asin: str | None,
        isbn: str | None = None,
        source_name: str,
    ) -> list[SourceResult]:
        """Query a single chosen source with the (user-edited) fields and return
        the results ranked against `book`. An unknown source name or a source
        error yields an empty list (logged)."""
        source = next((s for s in self.ctx.sources if s.name == source_name), None)
        if source is None:
            logger.warning(f"search_matches: unknown source {source_name!r}")
            return []
        query = SourceQuery(
            title=(title or "").strip() or None,
            author=(author or "").strip() or None,
            series=(series or "").strip() or None,
            asin=(asin or "").strip() or None,
            isbn=(isbn or "").strip() or None,
        )
        try:
            results = await source.search(query)
        except Exception as e:  # a source failing must not crash the search (BLE001 intentional)
            logger.warning(f"source {source_name} failed in search_matches: {e}")
            return []
        return self._score(book, results).ranked

    @staticmethod
    def match_field_values(result: SourceResult) -> dict[str, str | None]:
        """Map a source result's present fields to editable-field updates. The
        single source of truth for which fields a match offers (the UI picker and
        apply both consume this), so the two cannot drift."""
        updates: dict[str, str | None] = {}
        if result.title:
            updates["title"] = result.title
        if result.subtitle:
            updates["subtitle"] = result.subtitle
        if result.authors:
            updates["author"] = "; ".join(result.authors)
        if result.narrators:
            updates["narrator"] = "; ".join(result.narrators)
        if result.series_name:
            updates["series"] = result.series_name
        if result.series_sequence is not None:
            updates["sequence"] = str(result.series_sequence)
        if result.publish_year is not None:
            updates["year"] = str(result.publish_year)
        if result.asin:
            updates["asin"] = result.asin
        if result.isbn:
            updates["isbn"] = result.isbn
        if result.publisher:
            updates["publisher"] = result.publisher
        if result.language:
            updates["language"] = result.language
        if result.description:
            updates["description"] = result.description
        if result.genres:
            updates["genre"] = "; ".join(result.genres)
        if result.tags:
            updates["tag"] = "; ".join(result.tags)
        return updates

    def _merge_genre_tag_updates(
        self, book: BookUnit, result: SourceResult, updates: dict[str, str | None]
    ) -> None:
        """Rewrite any genre/tag entries in `updates` to merge with the book's
        existing genres/tags (union, deduped) so applying a match never clobbers
        curated entries. Mutates `updates` in place. Genres dedupe
        case-insensitively (normalize_genres); tags dedupe exactly, order-preserving
        (merge_preserve)."""
        if "genre" in updates:
            incoming = self.genre_policy().canonicalize(result.genres)
            updates["genre"] = "; ".join(normalize_genres(book.genres + incoming)) or None
        if "tag" in updates:
            updates["tag"] = "; ".join(merge_preserve(book.tags, result.tags)) or None

    def _normalize_match_updates(self, updates: dict[str, str | None]) -> None:
        """Run the configured auto-normalize fields (config.normalize_on_match)
        through FIELD_NORMALIZERS in place. No-op for fields not selected, absent,
        None, or without a normalizer."""
        for field in self.ctx.config.normalize_on_match:
            normalizer = FIELD_NORMALIZERS.get(field)
            value = updates.get(field)
            if normalizer is not None and value:
                updates[field] = normalizer(value)

    def apply_match_fields(self, book: BookUnit, result: SourceResult, fields: set[str]) -> str:
        """Apply only the chosen fields from `result` (per-field selection), stamping
        the source as provenance. Returns the batch id of the editable-field changes
        (undoable). The pseudo-field "cover" captures result.cover_url onto the book
        (fetched/embedded later); that capture is persisted but is NOT part of the
        undoable batch."""
        if "cover" in fields and result.cover_url:
            book.cover_url = result.cover_url
        if result.abridged is not None:
            book.abridged = result.abridged
        updates = {k: v for k, v in self.match_field_values(result).items() if k in fields}
        self._merge_genre_tag_updates(book, result, updates)
        self._normalize_match_updates(updates)
        batch = apply_fields(self.ctx.books, self.ctx.history, book, updates, provenance=result.provider)
        # Re-score against the applied result so the book's confidence and state
        # reflect the match, consistent with Quick Match.
        # Note: Phase.MATCH is intentionally not marked here in v1 — match results
        # land on IDENTIFY via _rescore_after_match. MATCH is a reserved node in the
        # invalidation graph, pending the full match-phase wiring in a future release.
        self._rescore_after_match(book, [result])
        book.touch()
        self.ctx.books.upsert(book)
        self._sync_sidecar(book)
        self.invalidate(book, Phase.TAG)
        return batch

    def apply_match(self, book: BookUnit, result: SourceResult) -> str:
        """Apply all present fields from a chosen source result (and its cover)."""
        fields = set(self.match_field_values(result))
        if result.cover_url:
            fields.add("cover")
        return self.apply_match_fields(book, result, fields)

    # --- chapters ---
    async def apply_audnexus_chapters(
        self, book: BookUnit, asin: str | None = None
    ) -> ChapterApplyResult:
        """Fetch named chapters from Audnexus for `asin` (or book.asin), store them
        on the book, and report a runtime mismatch vs the source files."""
        target = (asin or book.asin or "").strip()
        if not target:
            return ChapterApplyResult(ok=False, error="no ASIN")
        source = next((s for s in self.ctx.sources if s.name == "audnexus"), None)
        fetch_chapters = getattr(source, "fetch_chapters", None)
        if fetch_chapters is None:
            return ChapterApplyResult(ok=False, error="Audible source not available")
        fetch = await fetch_chapters(target)
        if fetch is None or not fetch.chapters:
            return ChapterApplyResult(ok=False, error="no chapters found")
        book.chapters = fetch.chapters
        book.touch()
        self.ctx.books.upsert(book)
        self._sync_sidecar(book)
        source_runtime_ms = book.duration_ms
        return ChapterApplyResult(
            ok=True,
            count=len(fetch.chapters),
            audible_runtime_ms=fetch.runtime_ms,
            source_runtime_ms=source_runtime_ms,
            mismatch=runtime_mismatch(source_runtime_ms, fetch.runtime_ms),
        )

    def reset_chapters(self, book: BookUnit) -> None:
        """Clear stored chapters (revert to the file-boundary default)."""
        book.chapters = []
        book.touch()
        self.ctx.books.upsert(book)
        self._sync_sidecar(book)

    def save_chapters(self, book: BookUnit, chapters: list[Chapter]) -> None:
        """Persist hand-edited chapters, sorting by start and recomputing the ends
        against the book runtime so the stored timeline stays consistent."""
        book.chapters = normalize_chapters(chapters, book.duration_ms)
        book.touch()
        self.ctx.books.upsert(book)
        self._sync_sidecar(book)

    # --- encode + organize ---
    def ready_books(self) -> list[BookUnit]:
        return self.ctx.books.list_by_state(BookState.READY)

    def _encode_target(self, book: BookUnit) -> Path:
        """In-place output path for an encode: <source_folder>/<sanitized title>.m4b
        (falls back to the book id when there's no usable title)."""
        stem = sanitize_name(book.title or book.id) or book.id
        return book.source_folder / f"{stem}.m4b"

    def organize_targets(
        self, books: list[BookUnit], *, patterns: AudiobookPatterns | None = None
    ) -> list[tuple[str, Path]]:
        """Pure dry-run: the (book_id, target_path) each book would organize to, computed
        from `patterns` (or the saved patterns). Encodes/moves nothing."""
        pats = patterns or self.ctx.patterns
        root = self.ctx.config.library_root or (default_db_path().parent / "library")
        return [(b.id, build_target_path(root, pats, b)) for b in books]

    def _process_book(self, book: BookUnit, options: EncodeJobOptions) -> BookProcessResult:
        """Run the selected operations for one book: encode (in place, untagged) ->
        organize (move) -> tag once at the resting path -> optional source delete."""
        if options.encode:
            target = self._encode_target(book)
            if target.exists() and target != book.output_path:
                return BookProcessResult(book_id=book.id, status="failed", detail="output name collision")
            mark(book, Phase.ENCODE, PhaseState.RUNNING)
            resync_state(book)
            self.ctx.books.upsert(book)
            enc = encode_book(
                book, target, bitrate=self.ctx.config.transcode_bitrate,
                delete_sources=options.delete_sources, confirm_delete=options.delete_sources,
                chapters=book.chapters or None,
            )
            if not enc.verified or enc.output_path is None:
                mark(book, Phase.ENCODE, PhaseState.FAILED, detail=enc.error)
                resync_state(book)
                self.ctx.books.upsert(book)
                return BookProcessResult(book_id=book.id, status="failed", detail=enc.error)
            book.output_path = enc.output_path
            mark(book, Phase.ENCODE, PhaseState.FRESH)
            resync_state(book)
            book.touch()
            self.ctx.books.upsert(book)
        elif book.output_path is None or not book.output_path.exists():
            return BookProcessResult(book_id=book.id, status="skipped", detail="not encoded")

        if options.organize:
            library_root = self.ctx.config.library_root or (default_db_path().parent / "library")
            org = organize_book(
                self.ctx.books, book, book.output_path, root=library_root,
                patterns=options.patterns or self.ctx.patterns,
            )
            if not org.moved or org.target_path is None:
                mark(book, Phase.ORGANIZE, PhaseState.FAILED, detail=(org.error or "collision"))
                resync_state(book)
                book.touch()
                self.ctx.books.upsert(book)
                return BookProcessResult(
                    book_id=book.id, status="failed",
                    detail=("collision" if org.collision else org.error),
                )

        batch_id = new_batch_id()
        resting = book.output_path
        self.ctx.operations.record(OperationRecord(
            batch_id=batch_id, book_id=book.id, op_type=_OP_ORGANIZE,
            target=str(resting), before=None, outcome="ok",
        ))
        tag_file(resting, book, operations=self.ctx.operations, batch_id=batch_id)
        return BookProcessResult(book_id=book.id, status="done")

    async def run_encode_job(
        self,
        books: list[BookUnit],
        options: EncodeJobOptions,
        *,
        progress: Callable[[str, str], None] | None = None,
        cancel: CancelToken | None = None,
    ) -> EncodeJobResult:
        """Run the selected encode/organize/delete operations across `books` with
        bounded concurrency. Graceful cancel: a book not yet started after the token
        is set is reported 'cancelled'; in-flight books finish. `progress(book_id,
        status)` is called as each book moves through its steps."""
        sem = asyncio.Semaphore(max(1, options.concurrency))

        def _emit(book_id: str, status: str) -> None:
            if progress is not None:
                progress(book_id, status)

        async def _one(book: BookUnit) -> BookProcessResult:
            if cancel is not None and cancel.cancelled:
                _emit(book.id, "cancelled")
                return BookProcessResult(book_id=book.id, status="cancelled")
            async with sem:
                if cancel is not None and cancel.cancelled:
                    _emit(book.id, "cancelled")
                    return BookProcessResult(book_id=book.id, status="cancelled")
                _emit(book.id, "encoding" if options.encode else "organizing")
                if options.encode:
                    await self.ensure_cover_cached(book)
                result = await asyncio.to_thread(self._process_book, book, options)
                _emit(book.id, result.status)
                return result

        results = await asyncio.gather(*(_one(b) for b in books))
        return EncodeJobResult(results=list(results))

    def process_one(self, book: BookUnit, *, confirm_delete: bool = False) -> ProcessResult:
        """Encode + organize a single book (delegates to the unified worker, which
        handles encode-in-place, single-tag, and optional source delete)."""
        res = self._process_book(
            book, EncodeJobOptions(encode=True, organize=True, delete_sources=confirm_delete),
        )
        organized = res.status == "done" and book.state == BookState.ORGANIZED
        return ProcessResult(
            book_id=book.id,
            encoded=book.state in (BookState.ENCODED, BookState.ORGANIZED),
            organized=organized,
            detail=res.detail,
        )

    def process_ready(
        self,
        *,
        confirm_delete: bool = False,
        progress: Callable[[int, int, str], None] | None = None,
    ) -> list[ProcessResult]:
        ready = self.ready_books()
        total = len(ready)
        results: list[ProcessResult] = []
        for i, book in enumerate(ready, start=1):
            results.append(self.process_one(book, confirm_delete=confirm_delete))
            if progress is not None:
                progress(i, total, book.title or book.id)
        return results

    # --- external integrations (FR-7) ---
    async def trigger_abs_scan(self) -> bool:
        """Trigger an AudiobookShelf library scan. Returns False if ABS isn't
        configured or the scan failed (graceful degradation, FR-7.4)."""
        client = self.ctx.abs_client
        library_id = self.ctx.config.audiobookshelf_library_id
        if client is None or not library_id:
            return False
        try:
            await client.scan_library(library_id)
            return True
        except Exception as e:  # never let an integration failure crash the caller
            logger.warning(f"ABS scan failed: {e}")
            return False

    def import_ll_patterns(self, config_ini: Path) -> tuple[str, str]:
        """Read folder + single-file organize patterns from a LazyLibrarian
        config.ini, for the Settings importer. Returns (folder, file); raises
        FileNotFoundError when the path does not exist. The file pattern falls back
        to "$Title" (LazyLibrarian's multi-part audiobook_dest_file uses $Part/$Total,
        which are degenerate for Colophon's single-M4B output)."""
        if not config_ini.exists():
            raise FileNotFoundError(config_ini)
        pats = read_audiobook_patterns(config_ini)
        return pats.folder, (pats.single_file or "$Title")
