from pathlib import Path

from colophon.core.models import BookUnit, SeriesRef
from colophon.core.navigator import build_library_tree


def _book(bid: str, *, authors=None, series=None, title="", confidence=0.0) -> BookUnit:
    b = BookUnit.new(source_folder=Path("/x") / bid)
    b.id = bid
    b.title = title
    b.authors = authors or []
    b.series = series or []
    b.confidence = confidence
    return b


def test_multi_author_book_filed_under_each_author():
    b = _book("b1", authors=["Brandon Sanderson", "Janci Patterson"], title="Skyward")
    tree = build_library_tree([b])
    names = {a.name for a in tree.authors}
    assert names == {"Brandon Sanderson", "Janci Patterson"}
    for a in tree.authors:
        assert b in a.standalone  # appears under both


def test_authors_dedup_by_name_key():
    b1 = _book("b1", authors=["Robert A. Heinlein"], title="A")
    b2 = _book("b2", authors=["Heinlein, Robert A"], title="B")
    tree = build_library_tree([b1, b2])
    assert len(tree.authors) == 1  # order/period variants merge
    assert len(tree.authors[0].standalone) == 2


def test_series_sequence_ordering_preserved():
    b1 = _book("b1", authors=["BS"], series=[SeriesRef(name="Mistborn", sequence=2.0)])
    b2 = _book("b2", authors=["BS"], series=[SeriesRef(name="Mistborn", sequence=1.0)])
    tree = build_library_tree([b1, b2])
    s = tree.authors[0].series[0]
    assert [b.id for b in s.books] == ["b2", "b1"]  # sorted by sequence


def test_needs_id_is_no_author_and_no_series():
    b = _book("b1", title="Mystery")
    tree = build_library_tree([b])
    assert [x.id for x in tree.needs_id] == ["b1"]
    assert tree.authors == []


def test_needs_id_sorted_by_confidence():
    hi = _book("hi", title="High", confidence=40.0)
    lo = _book("lo", title="Low", confidence=10.0)
    tree = build_library_tree([hi, lo])
    assert [b.confidence for b in tree.needs_id] == [10.0, 40.0]  # ascending confidence


def test_series_without_author_files_under_series_name():
    # legacy fallback preserved: a book with a series but no author gets a pseudo-author
    # keyed by its first series name (so it still appears in the author view).
    b = _book("b1", series=[SeriesRef(name="Lonely Series", sequence=1.0)], title="Orphan")
    tree = build_library_tree([b])
    assert [a.name for a in tree.authors] == ["Lonely Series"]
    assert tree.needs_id == []  # has a series, so not needs_id


def test_duplicate_author_name_on_one_book_files_it_once():
    b = _book("b1", authors=["Brandon Sanderson", "Brandon Sanderson"], title="X")
    tree = build_library_tree([b])
    assert len(tree.authors) == 1
    assert [x.id for x in tree.authors[0].standalone] == ["b1"]  # not duplicated


def test_multi_series_multi_author_sequence_per_series():
    # a book in two series, under two authors: each series node orders by ITS own sequence
    b1 = _book("b1", authors=["A", "B"], series=[SeriesRef(name="S1", sequence=2.0)], title="One")
    b2 = _book("b2", authors=["A", "B"], series=[SeriesRef(name="S1", sequence=1.0)], title="Two")
    tree = build_library_tree([b1, b2])
    for a in tree.authors:  # both A and B see the same S1 ordering
        s1 = next(s for s in a.series if s.name == "S1")
        assert [b.id for b in s1.books] == ["b2", "b1"]


def test_all_books_is_unique_even_with_a_co_authored_book():
    b = _book("b1", authors=["A", "B"], title="X")
    tree = build_library_tree([b])
    assert [x.id for x in tree.all_books] == ["b1"]  # one entry despite two author memberships


def test_franchise_nodes_from_franchise_of_map():
    b1 = _book("b1", authors=["A"], title="One")
    b2 = _book("b2", authors=["B"], title="Two")
    tree = build_library_tree([b1, b2], franchise_of={"b1": "Cosmere", "b2": "Cosmere"})
    assert len(tree.franchises) == 1
    f = tree.franchises[0]
    assert f.name == "Cosmere" and {b.id for b in f.books} == {"b1", "b2"}


def test_no_franchise_nodes_without_map():
    b = _book("b1", authors=["A"], title="One")
    tree = build_library_tree([b])
    assert tree.franchises == []
