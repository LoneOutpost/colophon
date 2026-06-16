"""Identify a BookUnit against metadata sources and set its confidence + state."""

from __future__ import annotations

import asyncio
import logging

from colophon.adapters.repository.store import BookUnitRepo
from colophon.core.confidence import score_identification
from colophon.core.models import BookState, BookUnit
from colophon.core.sources import MetadataSource, SourceQuery, SourceResult

logger = logging.getLogger(__name__)


def _query_for(book: BookUnit) -> SourceQuery:
    author = book.authors[0] if book.authors else None
    return SourceQuery(title=book.title, author=author, asin=book.asin)


async def _safe_search(source: MetadataSource, query: SourceQuery) -> list[SourceResult]:
    try:
        return await source.search(query)
    except Exception:  # one source failing must not abort identification (BLE001 intentional)
        logger.warning(f"source {source.name} failed during identify", exc_info=True)
        return []


async def identify(
    repo: BookUnitRepo,
    book: BookUnit,
    sources: list[MetadataSource],
    *,
    threshold: float,
) -> BookUnit:
    """Score `book` against `sources`, persist the outcome, and return the updated book."""
    query = _query_for(book)
    gathered = await asyncio.gather(*(_safe_search(s, query) for s in sources))
    results: list[SourceResult] = [r for batch in gathered for r in batch]

    outcome = score_identification(book, results)
    book.confidence = outcome.confidence
    book.confidence_signals = outcome.signals

    has_identity = bool(book.authors) or bool(book.series)
    if outcome.confidence >= threshold and has_identity:
        book.state = BookState.READY
    else:
        book.state = BookState.NEEDS_REVIEW

    book.touch()
    repo.upsert(book)
    return book
