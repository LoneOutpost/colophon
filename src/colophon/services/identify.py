"""Identify a BookUnit against metadata sources and set its confidence + state."""

from __future__ import annotations

from colophon.adapters.repository.store import BookUnitRepo
from colophon.core.confidence import score_identification
from colophon.core.models import BookState, BookUnit
from colophon.core.sources import MetadataSource
from colophon.services.matching import gather_matches, query_for_book


async def identify(
    repo: BookUnitRepo,
    book: BookUnit,
    sources: list[MetadataSource],
    *,
    threshold: float,
) -> BookUnit:
    """Score `book` against `sources`, persist the outcome, and return the updated book."""
    results = await gather_matches(sources, query_for_book(book))

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
