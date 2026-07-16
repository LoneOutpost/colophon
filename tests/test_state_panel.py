from pathlib import Path

from colophon.core.models import BookUnit, EmbeddedTags, Phase, PhaseState
from colophon.core.phases import LOCAL, mark
from colophon.ui import state_panel
from colophon.ui.state_panel import (
    _PHASE_ICONS,
    _PHASE_LABELS,
    _PHASE_STATE_COLOR,
    embedded_tag_rows,
    phase_rows,
)


def test_phase_label_returns_display_names():
    assert state_panel.phase_label(Phase.IDENTIFY) == "Identify"
    assert state_panel.phase_label(Phase.ENCODE) == "Encode"


def test_local_phases_are_exactly_the_rerunnable_set():
    # The timeline enables re-run only for local phases.
    assert set(LOCAL) == {Phase.SEARCH, Phase.CATEGORIZE, Phase.IDENTIFY}


def test_phase_maps_cover_all_enum_members():
    # phase_rows indexes these with [] — a missing member would KeyError at render.
    assert set(_PHASE_LABELS) == set(Phase)
    assert set(_PHASE_ICONS) == set(Phase)
    assert set(_PHASE_STATE_COLOR) == set(PhaseState)


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


def test_embedded_tag_rows_omits_absent_tags_and_keeps_order():
    tags = EmbeddedTags(title="Cujo", artist="Stephen King", year=1981, track=1)
    rows = embedded_tag_rows(tags)
    assert rows == [
        ("Title", "Cujo"),
        ("Artist", "Stephen King"),
        ("Year", "1981"),
        ("Track", "1"),
    ]


def test_embedded_tag_rows_empty_when_no_tags():
    assert embedded_tag_rows(EmbeddedTags()) == []


def test_embedded_tag_rows_formats_whole_sequence_without_decimal():
    assert ("Sequence", "1") in embedded_tag_rows(EmbeddedTags(sequence=1.0))
    assert ("Sequence", "1.5") in embedded_tag_rows(EmbeddedTags(sequence=1.5))
