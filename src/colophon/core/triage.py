"""Pure triage predicates over a BookUnit (confidence / state / provenance / missing fields /
findings). UI-agnostic so the Library page's facet filters are unit-testable without a UI harness."""

from __future__ import annotations

from colophon.core.models import BookState, BookUnit

# States that do NOT need a human: finished work, or a deliberate skip.
_DONE_STATES = {BookState.READY, BookState.ORGANIZED, BookState.ENCODED, BookState.SKIPPED}
# Provenances that mean "inferred, not asserted" — the identity is a guess.
_WEAK_PROVENANCE = {"directory", "filename", "graphing"}
_CONF_LOW = 40.0   # < this -> "low" (red); matches the badge color thresholds
_CONF_HIGH = 75.0  # >= this -> "high" (green); the default ready threshold


def needs_human(book: BookUnit) -> bool:
    """A book that still needs attention — anything not finished or deliberately skipped."""
    return book.state not in _DONE_STATES


def confidence_bucket(book: BookUnit) -> str:
    """'low' (<40), 'mid' (40-74), or 'high' (>=75) — matching the confidence badge colors."""
    if book.confidence >= _CONF_HIGH:
        return "high"
    if book.confidence >= _CONF_LOW:
        return "mid"
    return "low"


def has_weak_identity(book: BookUnit) -> bool:
    """True when the author or series was only inferred (folder / filename / graph), not asserted
    by a tag, datafile, manual edit, or a metadata match — i.e. the identity is a guess."""
    prov = book.provenance
    return prov.get("authors") in _WEAK_PROVENANCE or prov.get("series") in _WEAK_PROVENANCE


def missing_fields(book: BookUnit) -> set[str]:
    """Which curation fields are absent: subset of {series, cover, ident, narrator, year}."""
    out: set[str] = set()
    if not book.series:
        out.add("series")
    if not book.cover_path and not book.cover_url:
        out.add("cover")
    if not book.asin and not book.isbn:
        out.add("ident")
    if not book.narrators:
        out.add("narrator")
    if book.publish_year is None:
        out.add("year")
    return out


def has_open_findings(book: BookUnit) -> bool:
    """True when the book has a structural finding the user hasn't acknowledged."""
    return any(f.code not in book.acknowledged_findings for f in book.findings)
