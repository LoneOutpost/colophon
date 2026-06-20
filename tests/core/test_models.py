from pathlib import Path

import pytest
from pydantic import ValidationError

from colophon.core.errors import ColophonError, IntegrityError
from colophon.core.models import (
    BookState,
    BookUnit,
    ConfidenceSignal,
    Provenance,
    SeriesRef,
    SourceFile,
    _now,
)


def test_integrity_error_is_colophon_error():
    assert issubclass(IntegrityError, ColophonError)
    with pytest.raises(ColophonError):
        raise IntegrityError("boom")


def test_source_file_round_trips_through_json():
    sf = SourceFile(
        path=Path("/ingest/book/01.mp3"),
        size=1234,
        duration_seconds=61.5,
        ext="mp3",
    )
    restored = SourceFile.model_validate_json(sf.model_dump_json())
    assert restored == sf
    assert restored.path == Path("/ingest/book/01.mp3")


def test_source_file_serializes_camelcase_on_the_wire():
    sf = SourceFile(path=Path("/x.mp3"), size=1, duration_seconds=2.0, ext="mp3")
    dumped = sf.model_dump(by_alias=True)
    assert "durationSeconds" in dumped
    assert "duration_seconds" not in dumped


def test_provenance_values():
    assert Provenance.TAG.value == "tag"
    assert Provenance.AUDNEXUS.value == "audnexus"


def test_confidence_signal_holds_points_and_detail():
    sig = ConfidenceSignal(name="asin_exact_match", points=60, detail="ASIN B07 resolved on Audnexus")
    assert sig.points == 60
    assert "Audnexus" in sig.detail


def test_book_unit_id_is_stable_hash_of_source_folder():
    a = BookUnit.new(source_folder=Path("/ingest/The Way of Kings"))
    b = BookUnit.new(source_folder=Path("/ingest/The Way of Kings"))
    c = BookUnit.new(source_folder=Path("/ingest/Dune"))
    assert a.id == b.id
    assert a.id != c.id
    assert len(a.id) == 16


def test_book_unit_id_is_normalized():
    base = BookUnit.new(source_folder=Path("/ingest/Dune"))
    trailing = BookUnit.new(source_folder=Path("/ingest/Dune/"))
    dot = BookUnit.new(source_folder=Path("/ingest/./Dune"))
    assert base.id == trailing.id == dot.id


def test_created_at_is_frozen():
    bu = BookUnit.new(source_folder=Path("/ingest/x"))
    with pytest.raises(ValidationError):
        bu.created_at = _now()


def test_book_unit_defaults_to_detected_state():
    bu = BookUnit.new(source_folder=Path("/ingest/x"))
    assert bu.state == BookState.DETECTED
    assert bu.confidence == 0.0
    assert bu.provenance == {}


def test_book_unit_round_trips_with_provenance_and_series():
    bu = BookUnit.new(source_folder=Path("/ingest/x"))
    bu.title = "The Way of Kings"
    bu.authors = ["Brandon Sanderson"]
    bu.series = [SeriesRef(name="Stormlight Archive", sequence=1.0)]
    bu.provenance = {"title": "tag", "authors": "audnexus", "series": "directory"}
    bu.confidence = 62.0
    restored = BookUnit.model_validate_json(bu.model_dump_json())
    assert restored == bu
    assert restored.provenance["authors"] == "audnexus"
    assert restored.series[0].sequence == 1.0


def test_touch_bumps_updated_at():
    bu = BookUnit.new(source_folder=Path("/ingest/x"))
    original_updated = bu.updated_at
    original_created = bu.created_at
    bu.touch()
    assert isinstance(bu.updated_at, type(original_updated))
    assert bu.updated_at >= original_updated
    assert bu.created_at == original_created


def test_book_state_has_full_pipeline_lifecycle():
    values = {s.value for s in BookState}
    assert values == {
        "detected",
        "identified",
        "needs_review",
        "ready",
        "encoding",
        "organized",
        "failed",
        "skipped",
    }


def test_book_unit_has_optional_output_path():
    from pathlib import Path as _P

    bu = BookUnit.new(source_folder=_P("/ingest/x"))
    assert bu.output_path is None
    bu.output_path = _P("/library/Author/Title/Title.m4b")
    restored = BookUnit.model_validate_json(bu.model_dump_json())
    assert restored.output_path == _P("/library/Author/Title/Title.m4b")


def test_book_unit_carries_cover_url_and_roundtrips_json():
    book = BookUnit.new(source_folder=Path("/x"))
    assert book.cover_url is None
    book.cover_url = "https://covers.example/abc-L.jpg"
    restored = BookUnit.model_validate_json(book.model_dump_json())
    assert restored.cover_url == "https://covers.example/abc-L.jpg"


def test_bookunit_genres_tags_default_empty(tmp_path):
    from colophon.core.models import BookUnit
    b = BookUnit.new(source_folder=tmp_path / "x")
    assert b.genres == []
    assert b.tags == []


def test_bookunit_loads_legacy_json_without_genres_tags(tmp_path):
    from colophon.core.models import BookUnit
    b = BookUnit.new(source_folder=tmp_path / "x")
    raw = b.model_dump(mode="json")
    raw.pop("genres", None)
    raw.pop("tags", None)
    import json
    restored = BookUnit.model_validate_json(json.dumps(raw))
    assert restored.genres == []
    assert restored.tags == []


def test_bookunit_genres_tags_round_trip(tmp_path):
    from colophon.core.models import BookUnit
    b = BookUnit.new(source_folder=tmp_path / "x")
    b.genres = ["Fantasy", "Epic"]
    b.tags = ["to-relisten"]
    restored = BookUnit.model_validate_json(b.model_dump_json())
    assert restored.genres == ["Fantasy", "Epic"]
    assert restored.tags == ["to-relisten"]
