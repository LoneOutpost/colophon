"""Pipeline phase tracking: ordering, marking, and the invalidation/cascade engine.

Pure with respect to I/O. Operates on a BookUnit's sparse `phases` map. `derive_state`
and `ensure_phases` (added in later tasks) translate to/from the legacy BookState.
"""

from __future__ import annotations

from datetime import UTC, datetime

from colophon.core.models import BookUnit, Phase, PhaseRecord, PhaseState

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
    return staled
