from pathlib import Path

from colophon.core.models import BookUnit, Phase, PhaseState
from colophon.core.phases import mark
from colophon.ui.state_panel import phase_rows


def test_phase_rows_are_in_pipeline_order_and_reflect_state():
    b = BookUnit.new(source_folder=Path("/x"))
    mark(b, Phase.SEARCH, PhaseState.FRESH)
    mark(b, Phase.IDENTIFY, PhaseState.FAILED, detail="boom")

    rows = phase_rows(b)
    assert [r.phase for r in rows] == list(Phase)        # all 7, pipeline order

    search = rows[0]
    assert search.phase is Phase.SEARCH
    assert search.label == "Search"
    assert search.state is PhaseState.FRESH
    assert search.color == "positive"

    ident = next(r for r in rows if r.phase is Phase.IDENTIFY)
    assert ident.state is PhaseState.FAILED
    assert ident.color == "negative"
    assert ident.detail == "boom"

    match = next(r for r in rows if r.phase is Phase.MATCH)
    assert match.state is PhaseState.PENDING             # missing record reads PENDING
    assert match.updated_at is None
