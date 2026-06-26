from pathlib import Path

from colophon.core.models import BookUnit, Phase, PhaseState
from colophon.core.phases import (
    DEFERRED,
    LOCAL,
    invalidate_from,
    mark,
    phases_from,
    state_of,
)


def _book():
    return BookUnit.new(source_folder=Path("/x"))


def test_phases_from_returns_phase_and_successors():
    assert phases_from(Phase.MATCH) == [Phase.MATCH, Phase.TAG, Phase.ORGANIZE, Phase.ENCODE]


def test_state_of_missing_is_pending():
    assert state_of(_book(), Phase.SEARCH) is PhaseState.PENDING


def test_invalidate_from_stales_phase_and_downstream_that_ran():
    b = _book()
    for p in Phase:
        mark(b, p, PhaseState.FRESH)
    staled = invalidate_from(b, Phase.IDENTIFY)
    assert state_of(b, Phase.IDENTIFY) is PhaseState.STALE
    assert state_of(b, Phase.MATCH) is PhaseState.STALE
    assert state_of(b, Phase.CATEGORIZE) is PhaseState.FRESH
    assert Phase.MATCH in staled


def test_invalidate_from_skips_pending_downstream():
    b = _book()
    mark(b, Phase.IDENTIFY, PhaseState.FRESH)
    invalidate_from(b, Phase.IDENTIFY)
    assert state_of(b, Phase.MATCH) is PhaseState.PENDING


def test_encode_override_not_staled_by_metadata_phase():
    b = _book()
    for p in Phase:
        mark(b, p, PhaseState.FRESH)
    invalidate_from(b, Phase.TAG)
    assert state_of(b, Phase.ORGANIZE) is PhaseState.STALE
    assert state_of(b, Phase.ENCODE) is PhaseState.FRESH


def test_encode_is_staled_by_search():
    b = _book()
    for p in Phase:
        mark(b, p, PhaseState.FRESH)
    invalidate_from(b, Phase.SEARCH)
    assert state_of(b, Phase.ENCODE) is PhaseState.STALE


def test_phase_classes():
    assert LOCAL == (Phase.SEARCH, Phase.CATEGORIZE, Phase.IDENTIFY)
    assert DEFERRED == (Phase.MATCH, Phase.TAG, Phase.ORGANIZE, Phase.ENCODE)
