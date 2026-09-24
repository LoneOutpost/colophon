from pathlib import Path

from colophon.core.models import BookState, BookUnit, Finding, FindingCode, FindingSeverity
from colophon.core.queue import build_queue, check_matches, nearest_classified, queue_reasons

ROOT = Path("/lib")
KINDS = {Path("/lib/Carole Stivers"): "author", Path("/lib/Carole Stivers/Mind Games"): "series"}


def kind_of(p: Path) -> str:
    return KINDS.get(p, "")


def _book(folder: str, *, state=BookState.IDENTIFIED, identity=70.0, authors=("Carole Stivers",),
          author_prov="tag", findings=(), confirmed=False, missing=False, title=None) -> BookUnit:
    b = BookUnit.new(source_folder=Path(folder))
    b.title = title or Path(folder).name
    b.authors = list(authors)
    if authors:
        b.provenance["authors"] = author_prov
    b.state, b.identity_confidence = state, identity
    b.findings = list(findings)
    b.manually_confirmed, b.missing = confirmed, missing
    return b


def _reasons(b):
    return queue_reasons(b, root=ROOT, kind_of=kind_of)


def test_a_well_backed_book_needs_nobody():
    assert _reasons(_book("/lib/Carole Stivers/The Mind Games")) == []


def test_nearest_classified_walks_up_to_the_first_matching_kind():
    got = nearest_classified(Path("/lib/Carole Stivers/Mind Games/Book 1"), ROOT, kind_of, {"author"})
    assert got == Path("/lib/Carole Stivers")
    assert nearest_classified(Path("/lib/Loose"), ROOT, kind_of, {"author"}) is None


def test_a_weak_author_from_the_folder_is_caused_by_that_author_folder():
    b = _book("/lib/Carole Stivers/The Mind Games", identity=55.0, author_prov="graphing")
    [r] = _reasons(b)
    assert r.kind == "weak"
    assert r.cause.kind == "folder" and r.cause.path == Path("/lib/Carole Stivers")


def test_a_weak_author_from_a_filename_is_the_books_own_problem():
    b = _book("/lib/Carole Stivers/The Mind Games", identity=55.0, author_prov="filename")
    [r] = _reasons(b)
    assert r.cause.kind == "book" and r.cause.book_id == b.id


def test_unsure_groups_under_the_nearest_entity_folder():
    b = _book("/lib/Carole Stivers/X", state=BookState.NEEDS_REVIEW, identity=30.0)
    kinds = [r.kind for r in _reasons(b)]
    assert kinds == ["unsure"]          # weak is suppressed: unsure already explains the low score


def test_an_author_conflict_groups_on_the_author_folder_and_suppresses_weak():
    f = Finding(code=FindingCode.METADATA_CONFLICT, severity=FindingSeverity.WARN,
                detail="author: tag 'A' vs folder 'Carole Stivers'")
    b = _book("/lib/Carole Stivers/X", identity=50.0, findings=[f])
    [r] = _reasons(b)
    assert r.kind == "finding" and r.cause.path == Path("/lib/Carole Stivers")


def test_a_confirmed_book_is_only_ever_queued_for_a_blocking_error():
    f = Finding(code=FindingCode.MIXED_QUALITY, severity=FindingSeverity.WARN, detail="x")
    assert _reasons(_book("/lib/a", identity=10.0, findings=[f], confirmed=True)) == []
    blocked = _book("/lib/Carole Stivers/gone", confirmed=True, missing=True)
    assert [r.kind for r in _reasons(blocked)] == ["blocked"]


def test_finished_and_skipped_books_are_not_queued():
    for s in (BookState.SKIPPED, BookState.ORGANIZED, BookState.ENCODED):
        assert _reasons(_book("/lib/a", state=s, identity=10.0)) == []


def test_books_sharing_a_cause_form_one_group_and_the_count_is_distinct_books():
    weak = [_book(f"/lib/Carole Stivers/B{i}", identity=55.0, author_prov="graphing")
            for i in range(3)]
    mq = Finding(code=FindingCode.MIXED_QUALITY, severity=FindingSeverity.WARN, detail="x")
    lone = _book("/lib/Carole Stivers/B9", findings=[mq])
    q = build_queue([*weak, lone], root_for=lambda _p: ROOT, kind_of=kind_of)
    assert [len(g.books) for g in q.groups] == [3, 1]          # biggest cause first
    assert q.groups[0].label == "3 books under Carole Stivers: author only from the folder"
    assert q.book_count == 4


def test_a_book_with_two_causes_appears_under_both_but_counts_once():
    mq = Finding(code=FindingCode.MIXED_QUALITY, severity=FindingSeverity.WARN, detail="x")
    b = _book("/lib/Carole Stivers/gone", findings=[mq], missing=True)
    q = build_queue([b], root_for=lambda _p: ROOT, kind_of=kind_of)
    assert [g.kind for g in q.groups] == ["blocked", "finding"]  # blocked always first
    assert q.book_count == 1


def test_check_matches_lists_weak_fits_worst_first():
    a, b, ok = _book("/lib/a"), _book("/lib/b"), _book("/lib/c")
    a.confidence, b.confidence, ok.confidence = 60.0, 40.0, 90.0
    confirmed = _book("/lib/d", confirmed=True)
    confirmed.confidence = 30.0
    assert check_matches([a, b, ok, confirmed], threshold=75.0) == [b, a]
