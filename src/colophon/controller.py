"""UI-agnostic orchestration of the Colophon pipeline. The UI calls only this."""

from __future__ import annotations

import asyncio
import logging
import uuid
from collections.abc import Callable
from pathlib import Path

from colophon.adapters.audio import is_audio_file
from colophon.adapters.config import Config, save_config
from colophon.adapters.realdebrid import RdUser, RealDebridClient
from colophon.adapters.sidecar import write_sidecar
from colophon.app_context import AppContext, default_db_path
from colophon.core.catalog import CatalogEntry, list_entries
from colophon.core.confidence import score_identification
from colophon.core.filename_parser import compile_template, parse_filename
from colophon.core.genre_policy import GenrePolicy
from colophon.core.models import BookState, BookUnit, EditChange, OperationRecord, Provenance, _Base
from colophon.core.navigator import AuthorNode, DirectoryListing, DirEntry, LibraryTree, SeriesNode
from colophon.core.normalize import FIELD_NORMALIZERS, merge_preserve, normalize_genres
from colophon.core.quickmatch import QuickMatchProposal, QuickMatchSummary
from colophon.core.sources import SourceQuery, SourceResult
from colophon.services import files as file_ops
from colophon.services.acquire import (
    AcquireCandidate,
    AcquireResult,
    download_torrent,
    list_candidates,
)
from colophon.services.catalog import apply_catalog_mapping
from colophon.services.cover import ensure_cached_cover
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
)
from colophon.services.identify import identify
from colophon.services.ingest import scan_ingest
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


def _cover_mime(path: Path) -> str:
    return "image/png" if path.suffix.lower() == ".png" else "image/jpeg"


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


class AppController:
    def __init__(self, ctx: AppContext) -> None:
        self.ctx = ctx

    def save_settings(self, config: Config) -> None:
        """Persist `config` to the config file and update the live context.

        Note: db_path changes take effect on next launch (the live DB
        connection is not rebuilt here)."""
        save_config(config, self.ctx.config_path)
        self.ctx.config = config

    # --- scanning / identification ---
    def scan(self, roots: list[Path] | None = None) -> int:
        roots = roots or self.ctx.config.scan_paths
        count = 0
        for root in roots:
            count += len(scan_ingest(
                self.ctx.books,
                root,
                template=self.ctx.config.filename_template,
                directory_scheme=self.ctx.config.directory_scheme,
            ))
        return count

    async def identify_pending(self) -> None:
        threshold = self.ctx.config.review_threshold
        for book in self.ctx.books.list_by_state(BookState.DETECTED):
            await identify(self.ctx.books, book, self.ctx.sources, threshold=threshold)

    # --- dashboard ---
    def dashboard_stats(self) -> dict[str, int]:
        books = self.ctx.books.list_all()
        stats = {"total": len(books)}
        for state in BookState:
            stats[state.value] = sum(1 for b in books if b.state == state)
        return stats

    def get_book(self, book_id: str) -> BookUnit | None:
        return self.ctx.books.get(book_id)

    async def book_cover(self, book_id: str) -> tuple[bytes, str] | None:
        """A book's cover image as (bytes, mime): the cached file if present, else
        fetched from `cover_url` and cached for next time. None when the book has
        no cover or the fetch fails."""
        book = self.get_book(book_id)
        if book is None:
            return None
        if book.cover_path and book.cover_path.exists():
            return book.cover_path.read_bytes(), _cover_mime(book.cover_path)
        if book.cover_url:
            path = await ensure_cached_cover(book, dest_dir=book.source_folder)
            if path is not None:
                self.ctx.books.upsert(book)  # remember the cache location
                return path.read_bytes(), _cover_mime(path)
        return None
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

    def known_authors(self) -> list[str]:
        """Distinct author names across the library, sorted (editor autocomplete)."""
        return sorted({a for b in self.ctx.books.list_all() for a in b.authors})

    def known_series(self) -> list[str]:
        """Distinct series names across the library, sorted (editor autocomplete)."""
        return sorted({s.name for b in self.ctx.books.list_all() for s in b.series})

    def genre_policy(self) -> GenrePolicy:
        """Build the active genre policy from config."""
        return GenrePolicy(
            mapping=self.ctx.config.genre_mapping,
            accepted=self.ctx.config.accepted_genres,
            whitelist_enabled=self.ctx.config.genre_whitelist_enabled,
        )

    def known_genres(self) -> list[str]:
        """Distinct genre names across the library, sorted (editor autocomplete)."""
        return sorted({g for b in self.ctx.books.list_all() for g in b.genres})

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
        return sorted({t for b in self.ctx.books.list_all() for t in b.tags})

    # --- workspace navigator ---
    def library_tree(self) -> LibraryTree:
        """Group all books into Author -> Series/standalone, plus a needs-id list."""
        books = self.ctx.books.list_all()
        needs_id = sorted(
            (b for b in books if not b.authors and not b.series),
            key=lambda b: b.confidence,
        )
        identified = [b for b in books if b.authors or b.series]

        by_author: dict[str, list[BookUnit]] = {}
        for b in identified:
            author = b.authors[0] if b.authors else b.series[0].name
            by_author.setdefault(author, []).append(b)

        authors: list[AuthorNode] = []
        for author in sorted(by_author):
            in_series: dict[str, list[BookUnit]] = {}
            standalone: list[BookUnit] = []
            for b in by_author[author]:
                if b.series:
                    in_series.setdefault(b.series[0].name, []).append(b)
                else:
                    standalone.append(b)
            series_nodes = [
                SeriesNode(
                    name=name,
                    books=sorted(
                        items,
                        key=lambda b: (
                            b.series[0].sequence if b.series and b.series[0].sequence is not None else 0.0
                        ),
                    ),
                )
                for name, items in sorted(in_series.items())
            ]
            authors.append(
                AuthorNode(
                    name=author,
                    series=series_nodes,
                    standalone=sorted(standalone, key=lambda b: b.title or ""),
                )
            )
        return LibraryTree(needs_id=needs_id, authors=authors)

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
        return batch

    def save_fields(self, book: BookUnit, updates: dict[str, str | None]) -> str:
        """Apply manual metadata edits to `book` in one batch and re-sync its
        sidecar. Returns the batch id (undoable via undo)."""
        batch = apply_fields(
            self.ctx.books, self.ctx.history, book, updates, provenance=Provenance.MANUAL.value
        )
        self._sync_sidecar(book)
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

    async def rd_download(
        self, torrent_id: str, *, progress: Callable[[int, int, str], None] | None = None,
    ) -> tuple[AcquireResult, list[str]]:
        """Download a torrent's audio/cover files, then ingest the folder. Returns
        the download result and the ids of any newly registered books."""
        client = self.rd_client()
        try:
            info = await client.torrent_info(torrent_id)
            result = await download_torrent(client, info, self._rd_download_dir(), progress=progress)
        finally:
            await client.aclose()
        book_ids: list[str] = []
        if result.any_ok:
            books = scan_ingest(
                self.ctx.books, result.folder,
                template=self.ctx.config.filename_template,
                directory_scheme=self.ctx.config.directory_scheme,
            )
            book_ids = [b.id for b in books]
        return result, book_ids

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

    def save_filename_pattern(self, pattern: str) -> None:
        """Add a validated `pattern` to the saved list (deduped) and persist.
        Raises ValueError if the pattern does not compile."""
        pat = pattern.strip()
        if not pat:
            return
        compile_template(pat)  # validate; raises ValueError on bad placeholders
        if pat in self.ctx.config.saved_filename_patterns:
            return
        self.ctx.config.saved_filename_patterns.append(pat)
        save_config(self.ctx.config, self.ctx.config_path)

    def remove_filename_pattern(self, pattern: str) -> None:
        """Remove `pattern` from the saved list and persist (no-op if absent)."""
        if pattern in self.ctx.config.saved_filename_patterns:
            self.ctx.config.saved_filename_patterns.remove(pattern)
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
                self.ctx.books.delete(BookUnit.new(source_folder=parent).id)
        return results

    @staticmethod
    def _has_direct_audio(folder: Path) -> bool:
        """True if `folder` directly contains at least one audio file."""
        try:
            return any(is_audio_file(c) for c in folder.iterdir() if c.is_file())
        except OSError:
            return False

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
            book_id = BookUnit.new(source_folder=r.destination.parent).id
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

    async def write_tags_books(self, books: list[BookUnit]) -> list[TagCommitResult]:
        """Fetch+cache each book's cover (best effort), then write tags into its
        files on a worker thread, logging every write for recovery. All books share
        one batch id, so a single undo reverts the whole selection."""
        batch_id = uuid.uuid4().hex
        results: list[TagCommitResult] = []
        for book in books:
            await ensure_cached_cover(book, dest_dir=book.source_folder)
            self.ctx.books.upsert(book)
            results.append(
                await asyncio.to_thread(
                    commit_tag, book, operations=self.ctx.operations, batch_id=batch_id
                )
            )
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
        return batch

    def bulk_normalize(self, books: list[BookUnit], fields: list[str]) -> str:
        """Normalize the given text `fields` across `books` in one undoable batch."""
        batch = _svc_bulk_normalize(
            self.ctx.books, self.ctx.history, books, fields, genre_policy=self.genre_policy()
        )
        for book in books:
            self._sync_sidecar(book)
        return batch

    def bulk_remap(self, books: list[BookUnit], *, src: str, dst: str, clear_source: bool) -> str:
        batch = _svc_bulk_remap(self.ctx.books, self.ctx.history, books, src=src, dst=dst, clear_source=clear_source)
        for book in books:
            self._sync_sidecar(book)
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
        book.state = BookState.READY
        book.touch()
        self.ctx.books.upsert(book)

    # --- match review / apply (FR-2.4, FR-3.3) ---
    async def get_matches(self, book: BookUnit) -> list[SourceResult]:
        """Re-query all sources for `book` and return candidate matches, best first."""
        results = await gather_matches(self.ctx.sources, query_for_book(book))
        return score_identification(book, results).ranked

    async def quick_match_scan(
        self,
        books: list[BookUnit],
        source_names: list[str],
        search_fields: set[str] | None = None,
    ) -> list[QuickMatchProposal]:
        """For each book, query the chosen sources, score the candidates, and
        return a proposal carrying the best result, all gathered results (for
        later re-scoring), and the scan confidence. Books are scanned concurrently.
        `search_fields` (when given) restricts which fields seed the query."""
        chosen = [s for s in self.ctx.sources if s.name in source_names]

        async def _scan(book: BookUnit) -> QuickMatchProposal:
            results = await gather_matches(chosen, query_for_book(book, search_fields))
            outcome = score_identification(book, results)
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
            if p.best.cover_url:
                p.book.cover_url = p.best.cover_url  # cover capture: persisted, not in batch
            if p.best.abridged is not None:
                p.book.abridged = p.best.abridged
            items.append((p.book, updates, p.best.provider))

        batch = bulk_apply_fields(self.ctx.books, self.ctx.history, items)

        now_ready = 0
        for p in applicable:
            now_ready += self._rescore_after_match(p.book, p.results)
            p.book.touch()
            self.ctx.books.upsert(p.book)
            self._sync_sidecar(p.book)

        return QuickMatchSummary(
            applied_count=len(applicable), now_ready_count=now_ready, batch_id=batch
        )

    def _rescore_after_match(self, book: BookUnit, results: list[SourceResult]) -> bool:
        """Re-score `book` against `results` and set its confidence, signals, and
        state (Ready when confident and it has an identity). Returns True if it is
        now Ready. Confidence/state are persisted by the caller (not part of the
        undoable field batch)."""
        outcome = score_identification(book, results)
        book.confidence = outcome.confidence
        book.confidence_signals = outcome.signals
        has_identity = bool(book.authors) or bool(book.series)
        ready = outcome.confidence >= self.ctx.config.review_threshold and has_identity
        book.state = BookState.READY if ready else BookState.NEEDS_REVIEW
        return ready

    def available_sources(self) -> list[tuple[str, str]]:
        """The configured metadata sources as (name, display label), in priority
        order, so the search dialog can list exactly the available services."""
        return [(s.name, _SOURCE_LABELS.get(s.name, s.name.title())) for s in self.ctx.sources]

    def source_label(self, name: str) -> str:
        """Human-facing label for a source/provenance name (e.g. 'audnexus' -> 'Audible')."""
        return _SOURCE_LABELS.get(name, name.title())

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
        return score_identification(book, results).ranked

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
        self._rescore_after_match(book, [result])
        book.touch()
        self.ctx.books.upsert(book)
        self._sync_sidecar(book)
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
        source_runtime_ms = round(sum(sf.duration_seconds for sf in book.source_files) * 1000)
        mismatch = abs(fetch.runtime_ms - source_runtime_ms) > 60_000
        return ChapterApplyResult(
            ok=True,
            count=len(fetch.chapters),
            audible_runtime_ms=fetch.runtime_ms,
            source_runtime_ms=source_runtime_ms,
            mismatch=mismatch,
        )

    def reset_chapters(self, book: BookUnit) -> None:
        """Clear stored chapters (revert to the file-boundary default)."""
        book.chapters = []
        book.touch()
        self.ctx.books.upsert(book)
        self._sync_sidecar(book)

    # --- encode + organize ---
    def ready_books(self) -> list[BookUnit]:
        return self.ctx.books.list_by_state(BookState.READY)

    def process_one(self, book: BookUnit, *, confirm_delete: bool = False) -> ProcessResult:
        library_root = self.ctx.config.library_root or (default_db_path().parent / "library")
        staging = library_root / ".staging"
        staging.mkdir(parents=True, exist_ok=True)

        book.state = BookState.ENCODING
        self.ctx.books.upsert(book)
        enc = encode_book(
            book, staging / f"{book.id}.m4b",
            bitrate=self.ctx.config.transcode_bitrate,
            delete_sources=confirm_delete, confirm_delete=confirm_delete,
            chapters=book.chapters or None,
        )
        if not enc.verified or enc.output_path is None:
            book.state = BookState.FAILED
            self.ctx.books.upsert(book)
            return ProcessResult(book_id=book.id, encoded=False, detail=enc.error)

        org = organize_book(self.ctx.books, book, enc.output_path, root=library_root, patterns=self.ctx.patterns)
        if not org.moved or org.target_path is None:
            book.state = BookState.FAILED
            book.touch()
            self.ctx.books.upsert(book)
            return ProcessResult(
                book_id=book.id, encoded=True, organized=False,
                detail=("collision" if org.collision else org.error),
            )

        # Embed tags into the M4B at its final location so the audit record's path
        # is truthful and any later revert targets the real file (FR-5.3 / FR-8.4).
        batch_id = uuid.uuid4().hex
        self.ctx.operations.record(OperationRecord(
            batch_id=batch_id, book_id=book.id, op_type=_OP_ORGANIZE,
            target=str(org.target_path), before=str(enc.output_path), outcome="ok",
        ))
        tag_file(org.target_path, book, operations=self.ctx.operations, batch_id=batch_id)
        return ProcessResult(book_id=book.id, encoded=True, organized=True)

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

    async def ll_lookup(self, term: str) -> list[dict]:
        """Read-only LazyLibrarian lookup; [] if unconfigured or unreachable."""
        client = self.ctx.ll_client
        if client is None:
            return []
        try:
            return await client.find_book(term)
        except Exception as e:  # never let an integration failure crash the caller
            logger.warning(f"LazyLibrarian lookup failed: {e}")
            return []
