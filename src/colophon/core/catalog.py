"""Library-wide vocabulary catalog: list the distinct authors, narrators, series,
genres, and tags across all books, and remap a value list for rename/merge/delete.

A "kind" is an editable field name (author/narrator/series/genre/tag). Operations
are applied through the field machinery so they reuse the undo system; series is
managed by its primary name."""

from __future__ import annotations

from collections import Counter

from colophon.core.fields import get_field
from colophon.core.models import BookUnit, _Base
from colophon.core.textlist import dedupe_preserving, split_list

CATALOG_KINDS = ("author", "narrator", "series", "genre", "tag", "publisher", "language")


class CatalogEntry(_Base):
    name: str
    count: int


def entry_names(book: BookUnit, kind: str) -> list[str]:
    """The distinct catalog values of `kind` on a single book, in order."""
    return dedupe_preserving(split_list(get_field(book, kind)))


def list_entries(books: list[BookUnit], kind: str) -> list[CatalogEntry]:
    """Distinct values of `kind` across `books` with per-book usage counts, sorted
    by name (case-insensitive)."""
    counter: Counter[str] = Counter()
    for book in books:
        for name in entry_names(book, kind):
            counter[name] += 1
    return [CatalogEntry(name=n, count=c) for n, c in sorted(counter.items(), key=lambda kv: kv[0].lower())]


def remap_names(names: list[str], mapping: dict[str, str | None]) -> list[str]:
    """Apply `mapping` (old -> new, or old -> None to drop) to `names`, deduping and
    preserving order."""
    mapped = (mapping[n] if n in mapping else n for n in names)
    return dedupe_preserving([n for n in mapped if n is not None])
