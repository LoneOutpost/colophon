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
    FACET_DEFAULTS,
    apply_facets,
    confidence_bucket,
    effective_confidence,
    has_open_findings,
    has_weak_identity,
    missing_fields,
    needs_human,
    sort_books,
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


def test_effective_confidence_prefers_match_then_identity():
    # a matched book ranks by its match score
    assert effective_confidence(_book(confidence=82.0, identity_confidence=40.0)) == 82.0
    # unmatched (confidence 0): fall back to local-identification confidence
    assert effective_confidence(_book(confidence=0.0, identity_confidence=90.0)) == 90.0
    # neither: 0
    assert effective_confidence(_book()) == 0.0


def test_confidence_bucket_uses_identity_pre_match():
    # unmatched but locally well-identified reads 'high', not a flat 'low'
    assert confidence_bucket(_book(confidence=0.0, identity_confidence=90.0)) == "high"
    assert confidence_bucket(_book(confidence=0.0, identity_confidence=30.0)) == "low"


def test_sort_worst_first_ranks_by_effective_confidence():
    strong = _book(title="A", confidence=0.0, identity_confidence=90.0)
    weak = _book(title="B", confidence=0.0, identity_confidence=20.0)
    assert sort_books([strong, weak], "conf_asc") == [weak, strong]
    assert sort_books([weak, strong], "conf_desc") == [strong, weak]


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


def test_facet_defaults_are_no_constraint():
    assert FACET_DEFAULTS == {"state": [], "confidence": [], "trust": None,
                              "missing": [], "findings": False}
    books = [_book(confidence=10.0), _book(confidence=90.0)]
    assert apply_facets(books, dict(FACET_DEFAULTS)) == books  # nothing filtered


def test_apply_facets_state_confidence_trust():
    low_weak = _book(state=BookState.NEEDS_REVIEW, confidence=20.0,
                     provenance={"authors": "graphing"})
    high_trusted = _book(state=BookState.READY, confidence=90.0,
                         provenance={"authors": "tag"})
    books = [low_weak, high_trusted]
    assert apply_facets(books, {**FACET_DEFAULTS, "confidence": ["low"]}) == [low_weak]
    assert apply_facets(books, {**FACET_DEFAULTS, "state": ["ready"]}) == [high_trusted]
    assert apply_facets(books, {**FACET_DEFAULTS, "trust": "weak"}) == [low_weak]
    assert apply_facets(books, {**FACET_DEFAULTS, "trust": "trusted"}) == [high_trusted]


def test_apply_facets_missing_and_findings():
    no_cover = _book(cover_path=None, cover_url=None, series=[SeriesRef(name="S")],
                     asin="A", narrators=["N"], publish_year=2020)
    has_cover = _book(cover_path=Path("/c.jpg"), series=[SeriesRef(name="S")],
                      asin="A", narrators=["N"], publish_year=2020)
    assert apply_facets([no_cover, has_cover], {**FACET_DEFAULTS, "missing": ["cover"]}) == [no_cover]

    f = Finding(code=FindingCode.LOOSE_IN_AUTHOR, severity=FindingSeverity.WARN, detail="x")
    flagged = _book(findings=[f])
    clean = _book()
    assert apply_facets([flagged, clean], {**FACET_DEFAULTS, "findings": True}) == [flagged]


def test_sort_books():
    a = _book(confidence=20.0, title="B")
    b = _book(confidence=80.0, title="A")
    assert sort_books([a, b], "conf_asc") == [a, b]
    assert sort_books([a, b], "conf_desc") == [b, a]
    assert sort_books([a, b], "title") == [b, a]
    assert sort_books([a, b], "none") == [a, b]   # unknown/none key -> unchanged order
