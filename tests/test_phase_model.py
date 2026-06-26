from datetime import UTC, datetime
from pathlib import Path

from colophon.core.models import BookUnit, Phase, PhaseRecord, PhaseState


def test_new_book_has_empty_phase_map_and_not_skipped():
    b = BookUnit.new(source_folder=Path("/x"))
    assert b.phases == {}
    assert b.skipped is False


def test_phase_record_roundtrips_on_the_book():
    b = BookUnit.new(source_folder=Path("/x"))
    b.phases[Phase.IDENTIFY] = PhaseRecord(state=PhaseState.FRESH, updated_at=datetime.now(UTC))
    loaded = BookUnit.model_validate(b.model_dump())
    assert loaded.phases[Phase.IDENTIFY].state is PhaseState.FRESH


def test_phase_order_is_declaration_order():
    assert list(Phase) == [
        Phase.SEARCH, Phase.CATEGORIZE, Phase.IDENTIFY,
        Phase.MATCH, Phase.TAG, Phase.ORGANIZE, Phase.ENCODE,
    ]
