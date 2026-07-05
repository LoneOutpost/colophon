from pathlib import Path

from colophon.core.models import (
    BookUnit,
    Finding,
    FindingCode,
    FindingSeverity,
    Provenance,
    SeriesRef,
)
from colophon.core.review import review_reasons


def _book(**kw) -> BookUnit:
    b = BookUnit.new(source_folder=Path("/lib/Some Author"))
    for k, v in kw.items():
        setattr(b, k, v)
    return b


def test_confident_book_has_no_reasons():
    b = _book(title="At Risk", authors=["Stella Rimington"], identity_confidence=90.0)
    b.provenance["authors"] = Provenance.TAG.value
    assert review_reasons(b) == []


def test_no_identity_is_a_reason():
    b = _book(title="Mystery")
    reasons = review_reasons(b)
    assert any("No author or series" in r for r in reasons)


def test_weak_author_only_when_confidence_is_low():
    low = _book(title="At Risk", authors=["Stella Rimington"], identity_confidence=20.0)
    low.provenance["authors"] = Provenance.DIRECTORY.value
    assert any("only guessed from the folder" in r for r in review_reasons(low))

    # same weak provenance, but the graph backs it (high confidence) -> not a reason
    strong = _book(title="At Risk", authors=["Stella Rimington"], identity_confidence=90.0)
    strong.provenance["authors"] = Provenance.DIRECTORY.value
    assert not any("only guessed" in r for r in review_reasons(strong))


def test_title_is_folder_name():
    b = _book(title="Some Author", authors=["Some Author"], identity_confidence=90.0)
    b.provenance["authors"] = Provenance.TAG.value
    assert any("just the folder name" in r for r in review_reasons(b))


def test_missing_title():
    b = _book(title=None, authors=["A"], identity_confidence=90.0)
    b.provenance["authors"] = Provenance.TAG.value
    assert any("No title" in r for r in review_reasons(b))


def test_structural_finding_surfaces_but_loose_in_author_does_not():
    mixed = _book(title="X", authors=["A"], identity_confidence=90.0,
                  findings=[Finding(code=FindingCode.MIXED_WORKS, severity=FindingSeverity.ERROR,
                                    detail="")])
    mixed.provenance["authors"] = Provenance.TAG.value
    assert any("mixes different books" in r for r in review_reasons(mixed))

    loose = _book(title="X", authors=["A"], identity_confidence=90.0,
                  findings=[Finding(code=FindingCode.LOOSE_IN_AUTHOR, severity=FindingSeverity.WARN,
                                    detail="")])
    loose.provenance["authors"] = Provenance.TAG.value
    assert review_reasons(loose) == []  # the normal loose-file layout is not a review reason


def test_empty_audio_finding_is_a_review_reason():
    b = _book(title="X", authors=["A"], identity_confidence=90.0,
              findings=[Finding(code=FindingCode.EMPTY_AUDIO, severity=FindingSeverity.ERROR,
                                detail="")])
    b.provenance["authors"] = Provenance.TAG.value
    reasons = review_reasons(b)
    assert any("no readable content" in r for r in reasons)


def test_acknowledged_finding_is_skipped():
    b = _book(title="X", authors=["A"], identity_confidence=90.0,
              findings=[Finding(code=FindingCode.MIXED_WORKS, severity=FindingSeverity.ERROR, detail="")],
              acknowledged_findings=[FindingCode.MIXED_WORKS])
    b.provenance["authors"] = Provenance.TAG.value
    assert review_reasons(b) == []


def test_reasons_are_deduped_and_ordered_structural_first():
    b = _book(title="Some Author",  # folder-name title (identity reason)
              findings=[Finding(code=FindingCode.MIXED_WORKS, severity=FindingSeverity.ERROR, detail="")])
    reasons = review_reasons(b)
    assert reasons[0] == "This folder mixes different books."          # structural first
    assert any("No author or series" in r for r in reasons)           # identity
    assert len(reasons) == len(set(reasons))                          # deduped


def test_series_only_inferred(_series="Liz Carlyle"):
    b = _book(title="At Risk", series=[SeriesRef(name=_series)], identity_confidence=20.0)
    b.provenance["series"] = Provenance.DIRECTORY.value
    assert any("series is only inferred" in r.lower() for r in review_reasons(b))
