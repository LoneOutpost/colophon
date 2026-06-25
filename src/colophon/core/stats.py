"""Pure library-statistics aggregation for the Stats view.

Computes top-line totals, a state breakdown, and top catalog entries from a list
of BookUnits. No I/O — the controller hands in the books, the UI renders the
result."""

from __future__ import annotations

from dataclasses import dataclass

from colophon.core.catalog import CatalogEntry, list_entries
from colophon.core.models import BookState, BookUnit


@dataclass
class LibraryStats:
    total_books: int
    total_duration_ms: int
    total_bytes: int
    by_state: list[tuple[BookState, int]]  # enum order, zero-count states omitted


def library_stats(books: list[BookUnit]) -> LibraryStats:
    """Top-line totals plus a per-state breakdown (in BookState enum order, with
    zero-count states dropped)."""
    counts: dict[BookState, int] = {}
    total_ms = 0
    total_bytes = 0
    for book in books:
        counts[book.state] = counts.get(book.state, 0) + 1
        total_ms += book.duration_ms
        total_bytes += sum(sf.size for sf in book.source_files)
    by_state = [(state, counts[state]) for state in BookState if state in counts]
    return LibraryStats(
        total_books=len(books),
        total_duration_ms=total_ms,
        total_bytes=total_bytes,
        by_state=by_state,
    )


def top_entries(books: list[BookUnit], kind: str, limit: int = 8) -> list[CatalogEntry]:
    """The most-used catalog values of `kind` (author/narrator/series/genre/tag),
    most books first, ties broken case-insensitively by name."""
    entries = list_entries(books, kind)
    entries.sort(key=lambda e: (-e.count, e.name.lower()))
    return entries[:limit]
