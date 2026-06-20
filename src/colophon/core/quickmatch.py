"""Data types for the bulk Quick Match flow: a per-book proposal produced by the
scan phase and a summary returned by the apply phase."""

from __future__ import annotations

from colophon.core.models import BookUnit, _Base
from colophon.core.sources import SourceResult


class QuickMatchProposal(_Base):
    """The best candidate found for one book during a Quick Match scan.

    `results` carries every gathered candidate so the apply phase can re-score
    the updated book without re-querying the sources. `best` is None when no
    source returned a candidate.
    """

    book: BookUnit
    best: SourceResult | None = None
    results: list[SourceResult] = []  # noqa: RUF012 - pydantic field default, copied per instance
    confidence: float = 0.0


class QuickMatchSummary(_Base):
    """Outcome of applying a set of proposals."""

    applied_count: int = 0
    now_ready_count: int = 0
    batch_id: str = ""
