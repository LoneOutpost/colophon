"""The detail panel's read-only State tab: a per-book diagnostics view of the
pipeline phases, identification scoring, classification signals, and findings.

`phase_rows` is the pure data shape (unit-tested); `render` (added later) builds the UI.
"""

from __future__ import annotations

from datetime import datetime
from typing import NamedTuple

from colophon.core.models import BookUnit, Phase, PhaseState
from colophon.core.phases import state_of

_PHASE_LABELS: dict[Phase, str] = {
    Phase.SEARCH: "Search",
    Phase.CATEGORIZE: "Categorize",
    Phase.IDENTIFY: "Identify",
    Phase.MATCH: "Match",
    Phase.TAG: "Tag",
    Phase.ORGANIZE: "Organize",
    Phase.ENCODE: "Encode",
}

# Static, non-status icons reused by the nav phase mode and the timeline.
_PHASE_ICONS: dict[Phase, str] = {
    Phase.SEARCH: "search",
    Phase.CATEGORIZE: "category",
    Phase.IDENTIFY: "fingerprint",
    Phase.MATCH: "compare_arrows",
    Phase.TAG: "sell",
    Phase.ORGANIZE: "folder_open",
    Phase.ENCODE: "graphic_eq",
}

_PHASE_STATE_COLOR: dict[PhaseState, str] = {
    PhaseState.PENDING: "grey-5",
    PhaseState.FRESH: "positive",
    PhaseState.STALE: "warning",
    PhaseState.RUNNING: "info",
    PhaseState.FAILED: "negative",
}


class PhaseRow(NamedTuple):
    phase: Phase
    label: str
    icon: str
    state: PhaseState
    color: str
    updated_at: datetime | None
    detail: str | None


def phase_rows(book: BookUnit) -> list[PhaseRow]:
    """One row per phase, in pipeline order, describing its current state for display."""
    rows: list[PhaseRow] = []
    for phase in Phase:
        record = book.phases.get(phase)
        st = state_of(book, phase)
        rows.append(PhaseRow(
            phase=phase,
            label=_PHASE_LABELS[phase],
            icon=_PHASE_ICONS[phase],
            state=st,
            color=_PHASE_STATE_COLOR[st],
            updated_at=record.updated_at if record is not None else None,
            detail=record.detail if record is not None else None,
        ))
    return rows
