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
    author_inferred: bool = False


class QuickMatchSummary(_Base):
    """Outcome of applying a set of proposals."""

    applied_count: int = 0
    now_ready_count: int = 0
    batch_id: str = ""


class IdentifyPlan(_Base):
    """A computed Identify run: per-candidate proposals plus the partition counts.
    Produced without persisting; consumed by apply_identify."""

    proposals: list[QuickMatchProposal] = []  # noqa: RUF012 - pydantic field default, copied per instance
    threshold: float = 0.0
    to_apply: int = 0
    to_review: int = 0
    skipped: int = 0


class IdentifySummary(_Base):
    """Outcome of applying an IdentifyPlan."""

    auto_matched: int = 0
    routed_to_review: int = 0
    batch_id: str = ""
