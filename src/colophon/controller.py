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
from colophon.core.confidence import score_identification
from colophon.core.filename_parser import compile_template, parse_filename
from colophon.core.models import BookState, BookUnit, OperationRecord, Provenance, _Base
from colophon.core.navigator import AuthorNode, DirectoryListing, DirEntry, LibraryTree, SeriesNode
from colophon.core.sources import SourceQuery, SourceResult
from colophon.services import files as file_ops
from colophon.services.acquire import (
    AcquireCandidate,
    AcquireResult,
    download_torrent,
    list_candidates,
)
from colophon.services.cover import ensure_cached_cover
from colophon.services.editing import (
    apply_fields,
    remap_field,
    set_field_value,
)
from colophon.services.editing import (
    bulk_remap as _svc_bulk_remap,
)
from colophon.services.editing import (
    bulk_set_field as _svc_bulk_set_field,
)
from colophon.services.encode import encode_book
from colophon.services.foster import FosterResult, foster_one
from colophon.services.identify import identify
from colophon.services.ingest import scan_ingest
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

# Display labels for the metadata sources shown in the match-search dialog.
_SOURCE_LABELS = {
    "audnexus": "Audnexus",
    "openlibrary": "OpenLibrary",
    "googlebooks": "Google Books",
    "hardcover": "Hardcover",
}


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

    def bulk_remap(self, books: list[BookUnit], *, src: str, dst: str, clear_source: bool) -> str:
        batch = _svc_bulk_remap(self.ctx.books, self.ctx.history, books, src=src, dst=dst, clear_source=clear_source)
        for book in books:
            self._sync_sidecar(book)
        return batch

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
        query = SourceQuery(
            title=book.title,
            author=book.authors[0] if book.authors else None,
            asin=book.asin,
            series=book.series[0].name if book.series else None,
        )

        async def _safe(source: object) -> list[SourceResult]:
            try:
                return await source.search(query)
            except Exception as e:  # one source failing must not sink the lookup
                logger.warning(f"source {getattr(source, 'name', '?')} failed in get_matches: {e}")
                return []

        gathered = await asyncio.gather(*(_safe(s) for s in self.ctx.sources))
        results = [r for batch in gathered for r in batch]
        return score_identification(book, results).ranked

    def available_sources(self) -> list[tuple[str, str]]:
        """The configured metadata sources as (name, display label), in priority
        order, so the search dialog can list exactly the available services."""
        return [(s.name, _SOURCE_LABELS.get(s.name, s.name.title())) for s in self.ctx.sources]

    async def search_matches(
        self,
        book: BookUnit,
        *,
        title: str | None,
        author: str | None,
        series: str | None,
        asin: str | None,
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
        if result.description:
            updates["description"] = result.description
        return updates

    def apply_match_fields(self, book: BookUnit, result: SourceResult, fields: set[str]) -> str:
        """Apply only the chosen fields from `result` (per-field selection), stamping
        the source as provenance. Returns the batch id of the editable-field changes
        (undoable). The pseudo-field "cover" captures result.cover_url onto the book
        (fetched/embedded later); that capture is persisted but is NOT part of the
        undoable batch."""
        if "cover" in fields and result.cover_url:
            book.cover_url = result.cover_url
        updates = {k: v for k, v in self.match_field_values(result).items() if k in fields}
        batch = apply_fields(self.ctx.books, self.ctx.history, book, updates, provenance=result.provider)
        self._sync_sidecar(book)
        return batch

    def apply_match(self, book: BookUnit, result: SourceResult) -> str:
        """Apply all present fields from a chosen source result (and its cover)."""
        fields = set(self.match_field_values(result))
        if result.cover_url:
            fields.add("cover")
        return self.apply_match_fields(book, result, fields)

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
