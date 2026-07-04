"""Pipeline phase tracking: ordering, marking, and the invalidation/cascade engine.

Pure with respect to I/O. Operates on a BookUnit's sparse `phases` map. `derive_state`
and `ensure_phases` (added in later tasks) translate to/from the legacy BookState.
"""

from __future__ import annotations

from datetime import UTC, datetime

from colophon.core.models import BookState, BookUnit, Phase, PhaseRecord, PhaseState

_ORDER = list(Phase)
LOCAL = (Phase.SEARCH, Phase.CATEGORIZE, Phase.IDENTIFY)
DEFERRED = (Phase.MATCH, Phase.TAG, Phase.ORGANIZE, Phase.ENCODE)

# Default: a phase is invalidated by ANY upstream phase. A phase may override to declare
# exactly what invalidates it (the seed of the per-phase dependency model). v1 overrides
# only ENCODE: it depends on the source audio + chapters, never on metadata.
INVALIDATED_BY: dict[Phase, set[Phase]] = {
    Phase.ENCODE: {Phase.SEARCH},
}


def phases_from(phase: Phase) -> list[Phase]:
    return _ORDER[_ORDER.index(phase):]


def phases_before(phase: Phase) -> list[Phase]:
    return _ORDER[: _ORDER.index(phase)]


def state_of(book: BookUnit, phase: Phase) -> PhaseState:
    rec = book.phases.get(phase)
    return rec.state if rec is not None else PhaseState.PENDING


def mark(book: BookUnit, phase: Phase, state: PhaseState, detail: str | None = None) -> None:
    book.phases[phase] = PhaseRecord(state=state, updated_at=datetime.now(UTC), detail=detail)


def invalidates(changed: Phase, target: Phase) -> bool:
    deps = INVALIDATED_BY.get(target, set(phases_before(target)))
    return changed in deps


def invalidate_from(book: BookUnit, phase: Phase) -> list[Phase]:
    """Stale `phase` and each later phase that declares it as an invalidator. Only phases
    that actually ran are staled (a PENDING phase stays PENDING). Returns the newly-staled
    downstream phases (excluding the named phase)."""
    mark(book, phase, PhaseState.STALE)
    staled: list[Phase] = []
    for p in phases_from(phase)[1:]:
        if state_of(book, p) is not PhaseState.PENDING and invalidates(phase, p):
            mark(book, p, PhaseState.STALE)
            staled.append(p)
    resync_state(book)
    return staled


DEFAULT_READY_THRESHOLD = 75.0   # 0-100 scale; matches config.review_threshold default
DEFAULT_IDENTITY_THRESHOLD = 60.0  # 0-100; local-identification confidence to read as IDENTIFIED


def derive_state(
    book: BookUnit, *, ready_threshold: float = DEFAULT_READY_THRESHOLD,
    identity_threshold: float = DEFAULT_IDENTITY_THRESHOLD,
) -> BookState:
    """The BookState from the phase map (+ non-phase signals). Two distinct confidences gate the
    identified/review distinction: `confidence` is the post-match verification score;
    `identity_confidence` is the pre-match local-identification confidence from the graph. A book the
    graph knows locally reads IDENTIFIED (not NEEDS_REVIEW) even before any source match."""
    if book.skipped:
        return BookState.SKIPPED
    if any(state_of(book, p) is PhaseState.FAILED for p in Phase):
        return BookState.FAILED
    if any(state_of(book, p) is PhaseState.RUNNING for p in Phase):
        return BookState.ENCODING
    if state_of(book, Phase.ORGANIZE) is PhaseState.FRESH:
        return BookState.ORGANIZED
    if state_of(book, Phase.ENCODE) is PhaseState.FRESH:
        return BookState.ENCODED
    if (state_of(book, Phase.IDENTIFY) is PhaseState.FRESH
            or state_of(book, Phase.MATCH) is PhaseState.FRESH):
        has_identity = bool(book.authors) or bool(book.series)
        if book.manually_confirmed or (book.confidence >= ready_threshold and has_identity):
            return BookState.READY          # source-verified or user-confirmed
        if has_identity and book.identity_confidence >= identity_threshold:
            return BookState.IDENTIFIED     # locally confident, awaiting a source match
        return BookState.NEEDS_REVIEW       # the graph genuinely isn't sure
    return BookState.DETECTED


def resync_state(
    book: BookUnit, *, ready_threshold: float = DEFAULT_READY_THRESHOLD,
    identity_threshold: float = DEFAULT_IDENTITY_THRESHOLD,
) -> None:
    """Recompute and store the denormalized BookState cache. Call after any phase mutation."""
    book.state = derive_state(
        book, ready_threshold=ready_threshold, identity_threshold=identity_threshold)


# Highest phase each legacy state implies is "done through".
_SEED_THROUGH: dict[BookState, Phase] = {
    BookState.ORGANIZED: Phase.ENCODE,
    BookState.ENCODED: Phase.ENCODE,
    BookState.READY: Phase.IDENTIFY,
    BookState.NEEDS_REVIEW: Phase.IDENTIFY,
    BookState.IDENTIFIED: Phase.IDENTIFY,
    BookState.FAILED: Phase.IDENTIFY,
    BookState.DETECTED: Phase.SEARCH,
}


def ensure_phases(book: BookUnit) -> None:
    """Lazily seed the phase map from the legacy `state` when it's empty (migration).
    No-op when phases are already present. Self-heals on the next scan/run."""
    if book.phases:
        return
    if book.state is BookState.SKIPPED:
        book.skipped = True
        through = Phase.SEARCH
    else:
        through = _SEED_THROUGH.get(book.state, Phase.SEARCH)
    for p in _ORDER:
        if _ORDER.index(p) <= _ORDER.index(through):
            mark(book, p, PhaseState.FRESH)
        else:
            break
    # ENCODED means the audio was produced but the book was NOT organized/moved.
    # The contiguous seed lights ORGANIZE (it precedes ENCODE in the enum), so clear it
    # back to PENDING so derive_state yields ENCODED, not ORGANIZED.
    if book.state is BookState.ENCODED:
        book.phases.pop(Phase.ORGANIZE, None)
    resync_state(book)
