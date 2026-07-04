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


def effective_confidence(book: BookUnit) -> float:
    """The confidence to rank and bucket a book by: its post-match verification score once matched,
    else its pre-match local-identification confidence. `confidence` is only ever nonzero after a
    source match or a manual confirmation, so it takes precedence when present; before that the graph
    tells us how well we know the book locally."""
    return book.confidence if book.confidence > 0 else book.identity_confidence


def confidence_bucket(book: BookUnit) -> str:
    """'low' (<40), 'mid' (40-74), or 'high' (>=75) — matching the confidence badge colors.
    Buckets on the effective confidence, so a locally-identified but unmatched book reads by how
    well the graph knows it, not as a flat 0."""
    conf = effective_confidence(book)
    if conf >= _CONF_HIGH:
        return "high"
    if conf >= _CONF_LOW:
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


# The "no constraint" facet selection. Copy with dict(FACET_DEFAULTS) before mutating.
FACET_DEFAULTS = {"state": [], "confidence": [], "trust": None, "missing": [], "findings": False}


def apply_facets(books: list[BookUnit], facets: dict) -> list[BookUnit]:
    """Keep books passing every active facet (AND across facets; OR within a multi-value facet).
    An empty list / None / False for a facet means it imposes no constraint."""
    state = set(facets.get("state") or ())
    confidence = set(facets.get("confidence") or ())
    trust = facets.get("trust")
    missing = set(facets.get("missing") or ())
    findings = bool(facets.get("findings"))

    out: list[BookUnit] = []
    for b in books:
        if state and b.state.value not in state:
            continue
        if confidence and confidence_bucket(b) not in confidence:
            continue
        if trust == "weak" and not has_weak_identity(b):
            continue
        if trust == "trusted" and has_weak_identity(b):
            continue
        if missing and not (missing & missing_fields(b)):
            continue
        if findings and not has_open_findings(b):
            continue
        out.append(b)
    return out


def sort_books(books: list[BookUnit], key: str) -> list[BookUnit]:
    """Order a book list. 'conf_asc' (worst first) / 'conf_desc' / 'title'; any other key
    (e.g. 'none') leaves the order unchanged. Confidence ties break by title."""
    if key == "conf_asc":
        return sorted(books, key=lambda b: (effective_confidence(b), (b.title or "").casefold()))
    if key == "conf_desc":
        return sorted(books, key=lambda b: (-effective_confidence(b), (b.title or "").casefold()))
    if key == "title":
        return sorted(books, key=lambda b: (b.title or "").casefold())
    return list(books)
