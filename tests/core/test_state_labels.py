"""The canonical human descriptions for book and phase states."""

import pytest

from colophon.core.models import BookState, BookUnit, PhaseState
from colophon.core.state_labels import (
    phase_state_description,
    state_badge_tooltip,
    state_description,
)


@pytest.mark.parametrize("state", list(BookState))
def test_every_book_state_has_description(state):
    assert state_description(state).strip()


@pytest.mark.parametrize("state", list(PhaseState))
def test_every_phase_state_has_description(state):
    assert phase_state_description(state).strip()


def test_descriptions_are_distinct():
    assert len({state_description(s) for s in BookState}) == len(list(BookState))
    assert len({phase_state_description(s) for s in PhaseState}) == len(list(PhaseState))


def test_badge_tooltip_finished_state_is_just_the_description(tmp_path):
    b = BookUnit.new(source_folder=tmp_path / "b")
    b.state = BookState.READY
    assert state_badge_tooltip(b) == state_description(BookState.READY)


def test_badge_tooltip_appends_review_reasons_when_uncertain(tmp_path):
    from colophon.core.models import Finding, FindingCode, FindingSeverity

    b = BookUnit.new(source_folder=tmp_path / "b")
    b.state = BookState.NEEDS_REVIEW
    b.findings = [
        Finding(code=FindingCode.MIXED_WORKS, severity=FindingSeverity.WARN, detail="x")
    ]
    tip = state_badge_tooltip(b)
    assert tip.startswith(state_description(BookState.NEEDS_REVIEW))
    assert len(tip) > len(state_description(BookState.NEEDS_REVIEW))
