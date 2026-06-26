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
    ready = _book(manually_confirmed=True)
    mark(ready, Phase.IDENTIFY, PhaseState.FRESH)
    assert derive_state(ready) is BookState.READY
    review = _book(confidence=0.0)
    mark(review, Phase.IDENTIFY, PhaseState.FRESH)
    assert derive_state(review) is BookState.NEEDS_REVIEW


def test_default_is_detected():
    assert derive_state(_book()) is BookState.DETECTED


def test_resync_writes_state_through():
    b = _book(manually_confirmed=True)
    mark(b, Phase.IDENTIFY, PhaseState.FRESH)
    resync_state(b)
    assert b.state is BookState.READY
