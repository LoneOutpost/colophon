"""Canonical human-readable descriptions for book and phase states, reused by every
badge so the wording never drifts. Pure: enum in, string out. Copy matches the
derive_state() logic in core/phases.py; plain punctuation, no em dashes."""

from __future__ import annotations

from colophon.core.models import BookState, BookUnit, PhaseState
from colophon.core.review import review_reasons

_STATE_DESCRIPTIONS: dict[BookState, str] = {
    BookState.DETECTED: "Scanned and readable, but not yet identified.",
    BookState.IDENTIFIED: (
        "Recognized from your library's structure and file tags, before matching an "
        "online source."
    ),
    BookState.NEEDS_REVIEW: (
        "Uncertain identification: low local confidence or weak metadata. Worth a look "
        "before matching a source."
    ),
    BookState.READY: (
        "Matched to a source at high confidence, or manually confirmed. Ready to process."
    ),
    BookState.ENCODING: "A processing step is running now.",
    BookState.ENCODED: "Audio encoded into an M4B, not yet organized into the library.",
    BookState.ORGANIZED: "Encoded and filed into the library.",
    BookState.FAILED: "A processing step failed. See the book's details.",
    BookState.SKIPPED: "You set this book aside. It won't be processed.",
}

_PHASE_STATE_DESCRIPTIONS: dict[PhaseState, str] = {
    PhaseState.PENDING: "Not started yet.",
    PhaseState.FRESH: "Completed and up to date.",
    PhaseState.STALE: "Completed earlier, but its inputs changed since. Re-run to refresh.",
    PhaseState.RUNNING: "Running now.",
    PhaseState.FAILED: "This step failed on its last run.",
}

# Finished/verified states: their badge tooltip is just the description, never a
# review reason (there is nothing to review).
_FINISHED_STATES = frozenset({BookState.READY, BookState.ORGANIZED, BookState.ENCODED})


def state_description(state: BookState) -> str:
    """One plain-language line explaining what a book state means."""
    return _STATE_DESCRIPTIONS[state]


def phase_state_description(state: PhaseState) -> str:
    """One plain-language line explaining what a phase state means."""
    return _PHASE_STATE_DESCRIPTIONS[state]


def state_badge_tooltip(book: BookUnit) -> str:
    """The full tooltip for a book's state badge: the state description, plus the
    specific review reasons when the book is uncertain (not a finished state)."""
    desc = state_description(book.state)
    if book.state in _FINISHED_STATES:
        return desc
    reasons = review_reasons(book)
    return f"{desc} {' '.join(reasons)}" if reasons else desc
