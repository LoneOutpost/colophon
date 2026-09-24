import time
from pathlib import Path

from colophon.core.models import (
    BookState,
    BookUnit,
    Finding,
    FindingCode,
    FindingSeverity,
    SeriesRef,
)
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


def test_unsure_suppresses_the_weak_reason():
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


def test_check_matches_excludes_blocked_books_and_zero_confidence():
    corrupt = Finding(code=FindingCode.EMPTY_AUDIO, severity=FindingSeverity.ERROR, detail="x")
    blocked = _book("/lib/a", findings=[corrupt])
    blocked.confidence = 20.0
    zero = _book("/lib/b")
    zero.confidence = 0.0
    weak = _book("/lib/c")
    weak.confidence = 50.0
    assert check_matches([blocked, zero, weak], threshold=75.0) == [weak]


def test_two_same_code_findings_count_the_book_once_in_its_group():
    f1 = Finding(code=FindingCode.EXTENSION_MISMATCH, severity=FindingSeverity.WARN, detail="a.mp3")
    f2 = Finding(code=FindingCode.EXTENSION_MISMATCH, severity=FindingSeverity.WARN, detail="b.mp3")
    b = _book("/lib/a", findings=[f1, f2])
    q = build_queue([b], root_for=lambda _p: ROOT, kind_of=kind_of)
    [group] = q.groups
    assert group.books == [b]
    assert q.book_count == 1


def test_multi_in_author_and_multi_in_undetermined_share_a_phrase_and_dedupe():
    fa = Finding(code=FindingCode.MULTI_IN_AUTHOR, severity=FindingSeverity.WARN, detail="x")
    fu = Finding(code=FindingCode.MULTI_IN_UNDETERMINED, severity=FindingSeverity.WARN, detail="y")
    b = _book("/lib/a", findings=[fa, fu])
    q = build_queue([b], root_for=lambda _p: ROOT, kind_of=kind_of)
    [group] = q.groups
    assert group.books == [b]
    assert q.book_count == 1


def test_review_queue_scales_to_a_large_flat_library_quickly():
    def flat_kind(_p: Path) -> str:
        return ""

    books = [_book(f"/lib/book{i}", missing=True) for i in range(12_000)]
    start = time.perf_counter()
    q = build_queue(books, root_for=lambda _p: ROOT, kind_of=flat_kind)
    elapsed = time.perf_counter() - start
    assert elapsed < 1.0, f"build_queue took {elapsed:.3f}s for 12,000 books"
    assert q.book_count == 12_000


def test_unsure_with_a_folder_derived_author_is_caused_by_that_author_folder():
    b = _book("/lib/Carole Stivers/X", state=BookState.NEEDS_REVIEW, identity=30.0,
              author_prov="graphing")
    [r] = _reasons(b)
    assert r.phrase == "identity unsure"
    assert r.cause.kind == "folder" and r.cause.path == Path("/lib/Carole Stivers")


def test_unsure_with_a_tag_author_under_an_author_folder_is_the_books_own_problem():
    # The folder did not supply the author, so confirming it cannot settle the book (a publisher
    # read as the title, say): the queue must not send the user to the Tree for it.
    b = _book("/lib/Carole Stivers/X", state=BookState.NEEDS_REVIEW, identity=55.0,
              title="Simon & Schuster")
    [r] = _reasons(b)
    assert r.phrase == "identity unsure"
    assert r.cause.kind == "book" and r.cause.book_id == b.id


def test_unsure_with_no_entity_ancestor_uses_the_book():
    b = _book("/lib/NoOne/Book1", state=BookState.NEEDS_REVIEW, identity=30.0)
    [r] = _reasons(b)
    assert r.kind == "unsure"
    assert r.cause.kind == "book" and r.cause.book_id == b.id


def test_an_unreadable_file_blocks_with_a_book_cause():
    corrupt = Finding(code=FindingCode.EMPTY_AUDIO, severity=FindingSeverity.ERROR, detail="bad file")
    b = _book("/lib/Carole Stivers/X", findings=[corrupt])
    [r] = _reasons(b)
    assert r.kind == "blocked"
    assert r.cause.kind == "book" and r.cause.book_id == b.id


def test_missing_book_with_no_entity_ancestor_uses_the_parent_folder():
    b = _book("/lib/Solo/Gone", missing=True)
    [r] = _reasons(b)
    assert r.kind == "blocked"
    assert r.cause.kind == "folder" and r.cause.path == Path("/lib/Solo")


def test_missing_book_at_the_scan_root_uses_the_root_as_its_own_cause():
    b = _book("/lib", missing=True)
    [r] = _reasons(b)
    assert r.kind == "blocked"
    assert r.cause.kind == "folder" and r.cause.path == ROOT


def test_clustered_books_in_one_folder_form_one_own_folder_group():
    folder = "/lib/Carole Stivers/Cluster"
    ma = Finding(code=FindingCode.MULTI_IN_AUTHOR, severity=FindingSeverity.WARN, detail="x")
    a, b = _book(folder, findings=[ma]), _book(folder, findings=[ma])
    a.id, b.id = "cluster-a", "cluster-b"
    q = build_queue([a, b], root_for=lambda _p: ROOT, kind_of=kind_of)
    [group] = q.groups
    assert group.cause.kind == "folder" and len(group.books) == 2


def test_clustered_books_with_a_book_scoped_finding_form_separate_groups():
    folder = "/lib/Carole Stivers/Cluster"
    mq_a = Finding(code=FindingCode.MIXED_QUALITY, severity=FindingSeverity.WARN, detail="x")
    mq_b = Finding(code=FindingCode.MIXED_QUALITY, severity=FindingSeverity.WARN, detail="x")
    a, b = _book(folder, findings=[mq_a]), _book(folder, findings=[mq_b])
    a.id, b.id = "cluster-c", "cluster-d"
    q = build_queue([a, b], root_for=lambda _p: ROOT, kind_of=kind_of)
    assert len(q.groups) == 2
    assert all(len(g.books) == 1 for g in q.groups)


def test_weak_series_reason_is_caused_by_the_series_folder():
    b = _book("/lib/Carole Stivers/Mind Games/Book1", identity=55.0)
    b.series = [SeriesRef(name="Mind Games")]
    b.provenance["series"] = "graphing"
    [r] = _reasons(b)
    assert r.kind == "weak" and r.phrase == "series only from the folder"
    assert r.cause.kind == "folder" and r.cause.path == Path("/lib/Carole Stivers/Mind Games")


def test_nearest_classified_folder_outside_root_is_none():
    assert nearest_classified(Path("/other/place"), ROOT, kind_of, {"author"}) is None


def test_nearest_classified_root_itself_unclassified_is_none():
    assert nearest_classified(ROOT, ROOT, kind_of, {"author"}) is None


def test_nearest_classified_root_itself_classified_is_root():
    def root_kind(p: Path) -> str:
        return "author" if p == ROOT else ""

    assert nearest_classified(ROOT, ROOT, root_kind, {"author"}) == ROOT


def test_an_acknowledged_finding_is_not_queued():
    f = Finding(code=FindingCode.MIXED_QUALITY, severity=FindingSeverity.WARN, detail="x")
    b = _book("/lib/a", findings=[f])
    b.acknowledged_findings = [f.key]
    assert _reasons(b) == []


def test_a_ready_book_with_an_active_finding_is_still_queued():
    f = Finding(code=FindingCode.MIXED_QUALITY, severity=FindingSeverity.WARN, detail="x")
    b = _book("/lib/a", state=BookState.READY, findings=[f])
    assert [r.kind for r in _reasons(b)] == ["finding"]


def test_a_folder_name_conflict_groups_its_books_on_the_author_folder():
    # The bulk-tagger case: every book agrees with the folder's elected value, so only the folder's
    # own name can say something is wrong, and that is one problem, not three.
    folder = Path("/lib/Neal Stephenson")

    def kinds(p: Path) -> str:
        return "author" if p == folder else ""

    detail = "folder name: folder 'Neal Stephenson' vs tags 'Top 100 Sci-Fi Books'"
    books = [_book(f"/lib/Neal Stephenson/Neal Stephenson.-.{t}", authors=("Top 100 Sci-Fi Books",),
                   findings=[Finding(code=FindingCode.METADATA_CONFLICT,
                                     severity=FindingSeverity.WARN, detail=detail)])
             for t in ("Cryptonomicon", "Anathem", "Seveneves")]
    q = build_queue(books, root_for=lambda _p: ROOT, kind_of=kinds)
    [group] = q.groups
    assert group.label == ("3 books under Neal Stephenson: "
                           "the folder's name disagrees with its books' tags")
    assert len(group.books) == 3
