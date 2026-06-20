"""Shared source-search helpers: build a query from a book and gather candidate
results across sources concurrently, swallowing per-source failures."""

from __future__ import annotations

import asyncio
import logging

from colophon.core.models import BookUnit
from colophon.core.sources import MetadataSource, SourceQuery, SourceResult

logger = logging.getLogger(__name__)


def query_for_book(book: BookUnit) -> SourceQuery:
    """A SourceQuery from a book's title, first author, first series, and asin."""
    author = book.authors[0] if book.authors else None
    series = book.series[0].name if book.series else None
    return SourceQuery(title=book.title, author=author, asin=book.asin, series=series)


async def _safe_search(source: MetadataSource, query: SourceQuery) -> list[SourceResult]:
    try:
        return await source.search(query)
    except Exception:  # one source failing must not abort the gather (BLE001 intentional)
        logger.warning(f"source {source.name} failed during search", exc_info=True)
        return []


async def gather_matches(
    sources: list[MetadataSource], query: SourceQuery
) -> list[SourceResult]:
    """Search all `sources` concurrently for `query`; a source that raises is
    logged and contributes no results. Returns the flattened candidate list."""
    gathered = await asyncio.gather(*(_safe_search(s, query) for s in sources))
    return [r for batch in gathered for r in batch]
