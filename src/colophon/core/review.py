"""Plain-language reasons a book is uncertain enough to warrant a human's review.

Pure over a BookUnit (and its findings). This is the book-level analogue of a directory node's
`kind_evidence`: it lets the Library say WHY a book reads "needs review" instead of only flagging it.
Each axiom inspects the book's own fields/provenance and, when its condition holds, contributes one
reason. Identity-provenance and structural findings are both surfaced, since either can be why a book
is unsure; the benign single-book-loose-in-author layout is deliberately NOT a reason.
"""

from __future__ import annotations

import re

from colophon.core.models import BookUnit, FindingCode
from colophon.core.phases import DEFAULT_IDENTITY_THRESHOLD

# Identity fields sourced from these tiers are guesses, not assertions (a tag/datafile/match/manual
# value is trusted). Kept in sync with triage._WEAK_PROVENANCE.
_WEAK_PROV = frozenset({"directory", "filename", "graphing"})

# Structural findings worth surfacing as a review reason. LOOSE_IN_AUTHOR (a single book sitting
# loose in an author folder) is the normal audiobook layout under the graph model, not a problem,
# so it is intentionally absent.
_FINDING_MSG: dict[FindingCode, str] = {
    FindingCode.MULTI_IN_AUTHOR: "Several distinct books share this folder.",
    FindingCode.MULTI_IN_UNDETERMINED: "Several distinct books share this folder.",
    FindingCode.MIXED_WORKS: "This folder mixes different books.",
    FindingCode.DUP_FORMAT: "The same book is here in more than one format.",
    FindingCode.DUP_EDITION: "More than one edition sits in this folder.",
    FindingCode.STRUCTURE_UNCLEAR: "The folder's structure could not be worked out.",
}


def _norm(text: str) -> str:
    """Case/space-insensitive key for comparing a title to a folder name."""
    return re.sub(r"\s+", " ", text.strip().casefold())


def review_reasons(
    book: BookUnit, *, identity_threshold: float = DEFAULT_IDENTITY_THRESHOLD
) -> list[str]:
    """Ordered, de-duplicated reasons this book is uncertain — structural first, then identity, then
    title. Empty when nothing is wrong (a confidently-identified or source-verified book)."""
    reasons: list[str] = []
    seen: set[str] = set()

    def add(msg: str) -> None:
        if msg not in seen:
            seen.add(msg)
            reasons.append(msg)

    # Structural concerns (skip user-acknowledged findings and the benign loose-in-author case).
    for f in book.findings:
        if f.code in book.acknowledged_findings:
            continue
        msg = _FINDING_MSG.get(f.code)
        if msg:
            add(msg)

    # Identity concerns.
    has_author, has_series = bool(book.authors), bool(book.series)
    if not has_author and not has_series:
        add("No author or series could be identified.")
    elif book.identity_confidence < identity_threshold:
        if has_author and book.provenance.get("authors") in _WEAK_PROV:
            add("The author is only guessed from the folder or filename, not confirmed.")
        elif has_series and book.provenance.get("series") in _WEAK_PROV:
            add("The series is only inferred, not confirmed.")

    # Title concerns.
    if not book.title:
        add("No title could be read from the file.")
    elif book.source_folder is not None and _norm(book.title) == _norm(book.source_folder.name):
        add("The title is just the folder name — the file(s) didn't supply one.")

    return reasons
