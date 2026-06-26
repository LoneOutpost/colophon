from pathlib import Path

from colophon.core.models import BookState, BookUnit, Phase, PhaseState
from colophon.core.phases import ensure_phases, state_of


def _book(state):
    b = BookUnit.new(source_folder=Path("/x"))
    b.state = state
    return b


def test_organized_seeds_through_organize():
    b = _book(BookState.ORGANIZED)
    ensure_phases(b)
    assert state_of(b, Phase.ORGANIZE) is PhaseState.FRESH
    assert state_of(b, Phase.ENCODE) is PhaseState.FRESH
    assert state_of(b, Phase.SEARCH) is PhaseState.FRESH


def test_detected_seeds_only_search():
    b = _book(BookState.DETECTED)
    ensure_phases(b)
    assert state_of(b, Phase.SEARCH) is PhaseState.FRESH
    assert state_of(b, Phase.CATEGORIZE) is PhaseState.PENDING


def test_needs_review_seeds_through_identify():
    b = _book(BookState.NEEDS_REVIEW)
    ensure_phases(b)
    assert state_of(b, Phase.IDENTIFY) is PhaseState.FRESH
    assert state_of(b, Phase.MATCH) is PhaseState.PENDING


def test_skipped_sets_flag():
    b = _book(BookState.SKIPPED)
    ensure_phases(b)
    assert b.skipped is True


def test_encoded_seeds_encode_but_not_organize():
    b = _book(BookState.ENCODED)
    ensure_phases(b)
    assert state_of(b, Phase.ENCODE) is PhaseState.FRESH
    assert state_of(b, Phase.ORGANIZE) is PhaseState.PENDING    # NOT organized
    assert b.state is BookState.ENCODED                          # derives correctly


def test_ensure_is_idempotent_noop_when_already_populated():
    b = _book(BookState.DETECTED)
    ensure_phases(b)
    before = dict(b.phases)
    ensure_phases(b)        # second call must not change anything
    assert dict(b.phases) == before
