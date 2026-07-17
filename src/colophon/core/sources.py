"""Normalized metadata-source query/result types and the source protocol."""

from __future__ import annotations

from typing import Protocol, runtime_checkable

from colophon.core.models import _Base

# Providers whose ASIN is an AUDIOBOOK (Audible) ASIN. A physical/Kindle ASIN from a book source
# (Hardcover, OpenLibrary, ...) must not be stored as the book's asin: it's the wrong product for an
# audiobook app, and it dead-ends the Audnexus/Audible lookup (which fetches /books/{asin}). We gate
# by SOURCE, not by parsing the ASIN — a Kindle ASIN and an Audible ASIN are indistinguishable as
# strings (both `B0…`).
AUDIOBOOK_ASIN_PROVIDERS: frozenset[str] = frozenset({"audnexus", "audible"})


class SourceQuery(_Base):
    title: str | None = None
    author: str | None = None
    asin: str | None = None
    isbn: str | None = None
    series: str | None = None


class SourceResult(_Base):
    """One normalized candidate from any metadata source."""

    provider: str
    title: str | None = None
    subtitle: str | None = None
    authors: list[str] = []  # noqa: RUF012 - pydantic field default, copied per instance
    narrators: list[str] = []  # noqa: RUF012 - pydantic field default, copied per instance
    series_name: str | None = None
    series_sequence: float | None = None
    publish_year: int | None = None
    asin: str | None = None
    isbn: str | None = None
    cover_url: str | None = None
    description: str | None = None
    publisher: str | None = None
    language: str | None = None
    genres: list[str] = []  # noqa: RUF012 - pydantic field default, copied per instance
    tags: list[str] = []  # noqa: RUF012 - pydantic field default, copied per instance
    runtime_ms: int | None = None
    abridged: bool | None = None
    raw: dict[str, object] = {}  # noqa: RUF012 - pydantic field default, copied per instance


@runtime_checkable
class MetadataSource(Protocol):
    """A metadata source. `name` identifies its provenance; `search` is async."""

    name: str

    async def search(self, query: SourceQuery) -> list[SourceResult]: ...


def arrange_sources(
    all_sources: list[MetadataSource], *, order: list[str], disabled: list[str]
) -> list[MetadataSource]:
    """Order `all_sources` by `order` (known names first, in that order; names not
    in `order` keep their incoming order, appended after); then drop any whose name
    is in `disabled`. Stale `order`/`disabled` names with no live source are ignored."""
    rank = {name: i for i, name in enumerate(order)}
    fallback = len(order)
    disabled_set = set(disabled)
    ordered = sorted(all_sources, key=lambda s: rank.get(s.name, fallback))
    return [s for s in ordered if s.name not in disabled_set]
