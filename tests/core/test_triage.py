from pathlib import Path

from colophon.core.models import (
    BookState,
    BookUnit,
    Finding,
    FindingCode,
    FindingSeverity,
    SeriesRef,
)
from colophon.core.triage import (
    confidence_bucket,
    has_open_findings,
    has_weak_identity,
    missing_fields,
    needs_human,
)


def _book(**kw) -> BookUnit:
    b = BookUnit.new(source_folder=Path("/lib/x"))
    for k, v in kw.items():
        setattr(b, k, v)
    return b


def test_needs_human_excludes_done_and_skipped():
    for s in (BookState.NEEDS_REVIEW, BookState.DETECTED, BookState.IDENTIFIED, BookState.FAILED):
        assert needs_human(_book(state=s))
    for s in (BookState.READY, BookState.ORGANIZED, BookState.ENCODED, BookState.SKIPPED):
        assert not needs_human(_book(state=s))


def test_confidence_bucket():
    assert confidence_bucket(_book(confidence=0.0)) == "low"
    assert confidence_bucket(_book(confidence=39.9)) == "low"
    assert confidence_bucket(_book(confidence=40.0)) == "mid"
    assert confidence_bucket(_book(confidence=74.9)) == "mid"
    assert confidence_bucket(_book(confidence=75.0)) == "high"
    assert confidence_bucket(_book(confidence=100.0)) == "high"


def test_has_weak_identity():
    assert has_weak_identity(_book(provenance={"authors": "graphing"}))
    assert has_weak_identity(_book(provenance={"series": "directory"}))
    assert has_weak_identity(_book(provenance={"authors": "filename"}))
    assert not has_weak_identity(_book(provenance={"authors": "tag"}))
    assert not has_weak_identity(_book(provenance={"authors": "manual", "series": "audnexus"}))
    assert not has_weak_identity(_book(provenance={}))


def test_missing_fields():
    full = _book(series=[SeriesRef(name="S")], cover_path=Path("/c.jpg"),
                 asin="B1", narrators=["N"], publish_year=2020)
    assert missing_fields(full) == set()
    assert missing_fields(_book()) == {"series", "cover", "ident", "narrator", "year"}
    assert "cover" not in missing_fields(_book(cover_url="http://x/c.jpg"))
    assert "ident" not in missing_fields(_book(isbn="9780000000000"))


def test_has_open_findings():
    f = Finding(code=FindingCode.LOOSE_IN_AUTHOR, severity=FindingSeverity.WARN, detail="x")
    assert has_open_findings(_book(findings=[f]))
    assert not has_open_findings(
        _book(findings=[f], acknowledged_findings=[FindingCode.LOOSE_IN_AUTHOR])
    )
    assert not has_open_findings(_book())
