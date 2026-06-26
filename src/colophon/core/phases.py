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


READY_THRESHOLD = 0.6   # align with the configured identification threshold


def derive_state(book: BookUnit) -> BookState:
    """The legacy BookState computed from the phase map (+ the two non-phase signals)."""
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
    if state_of(book, Phase.IDENTIFY) is PhaseState.FRESH:
        if book.manually_confirmed or book.confidence >= READY_THRESHOLD:
            return BookState.READY
        return BookState.NEEDS_REVIEW
    return BookState.DETECTED


def resync_state(book: BookUnit) -> None:
    """Recompute and store the denormalized BookState cache. Call after any phase mutation."""
    book.state = derive_state(book)


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
    resync_state(book)
