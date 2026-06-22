from pathlib import Path

from colophon.core.confidence import score_identification
from colophon.core.models import BookUnit
from colophon.core.sources import SourceResult


def _book(**kw) -> BookUnit:
    b = BookUnit.new(source_folder=Path("/ingest/x"))
    for k, v in kw.items():
        setattr(b, k, v)
    return b


def test_asin_exact_match_scores_near_certain():
    book = _book(title="Dune", authors=["Frank Herbert"], asin="B002V1A0WE")
    results = [SourceResult(provider="audnexus", title="Dune", authors=["Frank Herbert"], asin="B002V1A0WE")]
    outcome = score_identification(book, results)
    assert outcome.confidence >= 90
    assert any(s.name == "asin_exact_match" for s in outcome.signals)
    assert outcome.best is not None and outcome.best.asin == "B002V1A0WE"


def test_isbn_exact_match_fires_on_isbn10_vs_isbn13():
    # book carries an ISBN-10, the source result its equivalent ISBN-13.
    book = _book(title="Compilers", authors=["Aho"], isbn="0306406152")
    results = [SourceResult(provider="openlibrary", title="Compilers", authors=["Aho"], isbn="9780306406157")]
    outcome = score_identification(book, results)
    assert any(s.name == "isbn_exact_match" and s.points == 60 for s in outcome.signals)


def test_no_isbn_signal_when_absent_or_different():
    book = _book(title="Compilers", authors=["Aho"], isbn="0306406152")
    none_results = [SourceResult(provider="openlibrary", title="Compilers", authors=["Aho"])]
    diff_results = [SourceResult(provider="openlibrary", title="Compilers", authors=["Aho"], isbn="9780134685991")]
    assert not any(s.name == "isbn_exact_match" for s in score_identification(book, none_results).signals)
    assert not any(s.name == "isbn_exact_match" for s in score_identification(book, diff_results).signals)


def test_cross_source_agreement_without_asin_is_high_not_certain():
    book = _book(title="Dune", authors=["Frank Herbert"])
    results = [
        SourceResult(provider="openlibrary", title="Dune", authors=["Frank Herbert"]),
        SourceResult(provider="audnexus", title="Dune", authors=["Frank Herbert"]),
    ]
    outcome = score_identification(book, results)
    assert 50 <= outcome.confidence < 90
    assert any(s.name == "cross_source_agreement" for s in outcome.signals)


def test_no_results_is_zero_confidence():
    outcome = score_identification(_book(title="Mystery"), [])
    assert outcome.confidence == 0.0
    assert outcome.best is None


def test_disagreement_keeps_confidence_low():
    book = _book(title="The Way of Kings", authors=["Brandon Sanderson"])
    results = [SourceResult(provider="openlibrary", title="A Totally Different Book", authors=["Nobody"])]
    outcome = score_identification(book, results)
    assert outcome.confidence < 50


def test_asin_match_but_wrong_metadata_stays_mid_band():
    book = _book(title="Right Book", authors=["Right Author"], asin="B0MATCH")
    results = [SourceResult(provider="audnexus", title="Completely Different", authors=["Nobody"], asin="B0MATCH")]
    outcome = score_identification(book, results)
    # 15 (embedded_core) + 60 (asin) - 25 (disagreement) == 50.0
    assert outcome.confidence == 50.0
    assert any(s.name == "asin_exact_match" for s in outcome.signals)
    assert any(s.name == "disagreement_penalty" for s in outcome.signals)


def test_ranked_results_best_first():
    book = _book(title="Dune", authors=["Frank Herbert"])
    weak = SourceResult(provider="googlebooks", title="Dune Encyclopedia", authors=["Other"])
    strong = SourceResult(provider="audnexus", title="Dune", authors=["Frank Herbert"])
    outcome = score_identification(book, [weak, strong])
    assert outcome.ranked[0] is strong


def test_multiple_results_same_provider_not_counted_as_cross_source():
    book = _book(title="Hitchhikers Guide", authors=["Douglas Adams"])
    # two OpenLibrary editions both match well -> still ONE provider
    results = [
        SourceResult(provider="openlibrary", title="Hitchhikers Guide", authors=["Douglas Adams"]),
        SourceResult(provider="openlibrary", title="Hitchhikers Guide to the Galaxy", authors=["Douglas Adams"]),
    ]
    outcome = score_identification(book, results)
    assert not any(s.name == "cross_source_agreement" for s in outcome.signals)
    assert any(s.name == "single_source_match" for s in outcome.signals)


def test_distinct_providers_still_count_as_cross_source():
    book = _book(title="Dune", authors=["Frank Herbert"])
    results = [
        SourceResult(provider="openlibrary", title="Dune", authors=["Frank Herbert"]),
        SourceResult(provider="audnexus", title="Dune", authors=["Frank Herbert"]),
    ]
    outcome = score_identification(book, results)
    assert any(s.name == "cross_source_agreement" for s in outcome.signals)


def test_embedded_core_awarded_when_no_external_results():
    book = _book(title="Starship Titanic", authors=["Terry Jones"])
    outcome = score_identification(book, [])
    assert outcome.confidence == 15.0
    assert any(s.name == "embedded_core" for s in outcome.signals)
    assert outcome.best is None


def test_strong_two_source_agreement_reaches_review_threshold():
    book = _book(title="Dune", authors=["Frank Herbert"])  # no ASIN
    results = [
        SourceResult(provider="openlibrary", title="Dune", authors=["Frank Herbert"]),
        SourceResult(provider="audnexus", title="Dune", authors=["Frank Herbert"]),
    ]
    outcome = score_identification(book, results)
    assert outcome.confidence >= 75  # 15 embedded + 60 cross-source (no ASIN needed)


def test_perfect_cross_source_scores_full_sixty_points():
    book = _book(title="Dune", authors=["Frank Herbert"])
    results = [
        SourceResult(provider="openlibrary", title="Dune", authors=["Frank Herbert"]),
        SourceResult(provider="audnexus", title="Dune", authors=["Frank Herbert"]),
    ]
    sig = next(s for s in score_identification(book, results).signals if s.name == "cross_source_agreement")
    assert sig.points == 60  # round(60 * 1.0) -> scaling formula


def _book_with_duration(tmp_path, ms):
    from colophon.core.models import BookUnit, SourceFile
    b = BookUnit.new(source_folder=tmp_path / "x")
    b.title = "Dune"
    b.authors = ["Frank Herbert"]
    if ms:
        b.source_files = [SourceFile(path=tmp_path / "a.mp3", size=0, duration_seconds=ms / 1000, ext="mp3")]
    return b


def test_runtime_match_bonus(tmp_path):
    from colophon.core.confidence import score_identification
    from colophon.core.sources import SourceResult
    b = _book_with_duration(tmp_path, 8_000_000)
    r = SourceResult(provider="audnexus", title="Dune", authors=["Frank Herbert"], runtime_ms=8_100_000)
    out = score_identification(b, [r])
    assert any(s.name == "runtime_match" for s in out.signals)


def test_runtime_mismatch_penalty(tmp_path):
    from colophon.core.confidence import score_identification
    from colophon.core.sources import SourceResult
    b = _book_with_duration(tmp_path, 8_000_000)
    r = SourceResult(provider="audnexus", title="Dune", authors=["Frank Herbert"], runtime_ms=4_000_000)
    out = score_identification(b, [r])
    assert any(s.name == "runtime_mismatch" for s in out.signals)


def test_no_runtime_signal_without_book_duration(tmp_path):
    from colophon.core.confidence import score_identification
    from colophon.core.sources import SourceResult
    b = _book_with_duration(tmp_path, 0)
    r = SourceResult(provider="audnexus", title="Dune", authors=["Frank Herbert"], runtime_ms=8_000_000)
    out = score_identification(b, [r])
    assert not any(s.name.startswith("runtime_") for s in out.signals)


def test_runtime_breaks_ranking_tie(tmp_path):
    from colophon.core.confidence import score_identification
    from colophon.core.sources import SourceResult
    b = _book_with_duration(tmp_path, 8_000_000)
    far = SourceResult(provider="a", title="Dune", authors=["Frank Herbert"], runtime_ms=4_000_000)
    near = SourceResult(provider="b", title="Dune", authors=["Frank Herbert"], runtime_ms=8_050_000)
    out = score_identification(b, [far, near])
    assert out.best is near


def test_abridged_mismatch_penalty(tmp_path):
    from colophon.core.confidence import score_identification
    from colophon.core.sources import SourceResult
    b = _book_with_duration(tmp_path, 0)
    b.abridged = False
    r = SourceResult(provider="a", title="Dune", authors=["Frank Herbert"], abridged=True)
    out = score_identification(b, [r])
    assert any(s.name == "format_mismatch" for s in out.signals)
