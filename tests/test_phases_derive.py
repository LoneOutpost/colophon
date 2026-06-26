from pathlib import Path

from colophon.core.models import BookState, BookUnit, Phase, PhaseState
from colophon.core.phases import derive_state, mark, resync_state


def _book(**kw):
    b = BookUnit.new(source_folder=Path("/x"))
    for k, v in kw.items():
        setattr(b, k, v)
    return b


def test_skipped_wins():
    assert derive_state(_book(skipped=True)) is BookState.SKIPPED


def test_failed_phase_is_failed():
    b = _book()
    mark(b, Phase.SEARCH, PhaseState.FRESH)
    mark(b, Phase.CATEGORIZE, PhaseState.FAILED)
    assert derive_state(b) is BookState.FAILED


def test_running_is_encoding_label():
    b = _book()
    mark(b, Phase.ENCODE, PhaseState.RUNNING)
    assert derive_state(b) is BookState.ENCODING


def test_organized_then_encoded_precedence():
    b = _book()
    mark(b, Phase.ENCODE, PhaseState.FRESH)
    mark(b, Phase.ORGANIZE, PhaseState.FRESH)
    assert derive_state(b) is BookState.ORGANIZED
    b2 = _book()
    mark(b2, Phase.ENCODE, PhaseState.FRESH)
    assert derive_state(b2) is BookState.ENCODED


def test_identified_ready_vs_needs_review():
    # manual confirmation is READY regardless of confidence/identity
    confirmed = _book(manually_confirmed=True)
    mark(confirmed, Phase.IDENTIFY, PhaseState.FRESH)
    assert derive_state(confirmed) is BookState.READY

    # confident AND has identity -> READY (default threshold 75 on a 0-100 scale)
    ready = _book(confidence=80.0, authors=["Some Author"])
    mark(ready, Phase.IDENTIFY, PhaseState.FRESH)
    assert derive_state(ready) is BookState.READY

    # confident but NO identity -> NEEDS_REVIEW
    no_identity = _book(confidence=80.0)
    mark(no_identity, Phase.IDENTIFY, PhaseState.FRESH)
    assert derive_state(no_identity) is BookState.NEEDS_REVIEW

    # below threshold -> NEEDS_REVIEW
    low = _book(confidence=50.0, authors=["Some Author"])
    mark(low, Phase.IDENTIFY, PhaseState.FRESH)
    assert derive_state(low) is BookState.NEEDS_REVIEW


def test_ready_threshold_is_overridable():
    b = _book(confidence=60.0, authors=["A"])
    mark(b, Phase.IDENTIFY, PhaseState.FRESH)
    assert derive_state(b) is BookState.NEEDS_REVIEW            # default 75
    assert derive_state(b, ready_threshold=55.0) is BookState.READY


def test_default_is_detected():
    assert derive_state(_book()) is BookState.DETECTED


def test_resync_writes_state_through():
    b = _book(manually_confirmed=True)
    mark(b, Phase.IDENTIFY, PhaseState.FRESH)
    resync_state(b)
    assert b.state is BookState.READY
