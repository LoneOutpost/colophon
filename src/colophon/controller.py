"""UI-agnostic orchestration of the Colophon pipeline. The UI calls only this."""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Callable
from pathlib import Path

from colophon.adapters.config import Config, save_config
from colophon.adapters.sidecar import write_sidecar
from colophon.app_context import AppContext, default_db_path
from colophon.core.confidence import score_identification
from colophon.core.models import BookState, BookUnit, _Base
from colophon.core.sources import SourceQuery, SourceResult
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
from colophon.services.identify import identify
from colophon.services.ingest import scan_ingest
from colophon.services.organize import organize_book
from colophon.services.undo import undo_batch

logger = logging.getLogger(__name__)


class TriageGroup(_Base):
    label: str
    books: list[BookUnit] = []  # noqa: RUF012 - pydantic field default, copied per instance


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
            count += len(scan_ingest(self.ctx.books, root, template=self.ctx.config.filename_template))
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

    # --- triage ---
    def triage_groups(self, *, flat: bool = False) -> list[TriageGroup]:
        books = self.ctx.books.list_all()
        if flat:
            ordered = sorted(books, key=lambda b: b.confidence)
            return [TriageGroup(label="All", books=ordered)]

        needs_id = [b for b in books if not b.authors and not b.series]
        identified = [b for b in books if b.authors or b.series]
        groups: list[TriageGroup] = []
        if needs_id:
            groups.append(TriageGroup(label="Needs identification", books=sorted(needs_id, key=lambda b: b.confidence)))
        by_author: dict[str, list[BookUnit]] = {}
        for b in identified:
            author = b.authors[0] if b.authors else (b.series[0].name if b.series else "Unknown")
            by_author.setdefault(author, []).append(b)
        for author in sorted(by_author):
            books_for = sorted(by_author[author], key=lambda b: (b.series[0].sequence if b.series and b.series[0].sequence is not None else 0.0))
            groups.append(TriageGroup(label=author, books=books_for))
        return groups

    def get_book(self, book_id: str) -> BookUnit | None:
        return self.ctx.books.get(book_id)

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

    def apply_match(self, book: BookUnit, result: SourceResult) -> str:
        """Apply a chosen source result's fields to `book` (undoable), stamping the
        source as provenance, and re-sync the sidecar."""
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
        batch = apply_fields(self.ctx.books, self.ctx.history, book, updates, provenance=result.provider)
        self._sync_sidecar(book)
        return batch

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
        if not org.moved:
            book.state = BookState.FAILED
            book.touch()
            self.ctx.books.upsert(book)
        return ProcessResult(
            book_id=book.id, encoded=True, organized=org.moved,
            detail=("collision" if org.collision else org.error),
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
