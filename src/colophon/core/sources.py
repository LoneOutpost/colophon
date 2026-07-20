"""Normalized metadata-source query/result types and the source protocol."""

from __future__ import annotations

from collections.abc import Iterable
from typing import Protocol, runtime_checkable

from colophon.core.models import _Base

# Audiobook-exclusive sources: every field they return describes the audiobook, so all of it is
# trusted by default (including the ASIN, which is the Audible product and feeds the Audnexus
# /books/{asin} lookup). A non-audiobook or mixed source (Hardcover, Google Books, Storytel, ...)
# describes the wrong product for the edition-specific fields below. Hardcoded and gated by SOURCE:
# a Kindle ASIN and an Audible ASIN are indistinguishable as strings (both `B0…`). An unrecognized
# provider is treated as non-audiobook (conservative). Keep in sync with the abs-agg provider ids.
AUDIOBOOK_PROVIDERS: frozenset[str] = frozenset({
    "audnexus", "audible",           # Audible (native)
    "soundbooththeater", "audioteka", "librofm", "graphicaudio", "librivox",
    "bigfinish", "dreifragezeichen",  # audiobook-exclusive abs-agg providers
})

# Fields that describe a specific edition/format, so they're only reliable from an audiobook source.
# From a non-audiobook source they are offered but left unchecked by default (opt-in) when strict.
EDITION_SPECIFIC_FIELDS: frozenset[str] = frozenset({"publisher", "isbn"})


def unchecked_edition_fields(provider: str, offered: Iterable[str], *, strict: bool) -> set[str]:
    """The offered fields that should be shown but left UNCHECKED by default: the edition-specific
    fields (publisher, ISBN) from a source that is not audiobook-exclusive, when `strict` is on. An
    audiobook source is trusted for every field; a print/mixed source's edition data describes the
    wrong product. Empty when `strict` is off or the provider is an audiobook source."""
    if not strict or provider in AUDIOBOOK_PROVIDERS:
        return set()
    return {f for f in offered if f in EDITION_SPECIFIC_FIELDS}


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
