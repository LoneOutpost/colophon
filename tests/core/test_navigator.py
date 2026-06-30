from pathlib import Path

from colophon.core.graph_resolve import _name_key
from colophon.core.models import BookUnit, SeriesRef
from colophon.core.navigator import build_library_tree, resolve_alias


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


def test_resolve_alias_passthrough_when_empty():
    assert resolve_alias({}, "author", "Brandon Sanderson") == "Brandon Sanderson"
    assert resolve_alias(None, "author", "Brandon Sanderson") == "Brandon Sanderson"


def test_resolve_alias_single_hop():
    aliases = {("author", _name_key("B. Sanderson")): "Brandon Sanderson"}
    assert resolve_alias(aliases, "author", "B. Sanderson") == "Brandon Sanderson"


def test_resolve_alias_follows_chain():
    aliases = {("author", _name_key("A")): "B", ("author", _name_key("B")): "C"}
    assert resolve_alias(aliases, "author", "A") == "C"


def test_resolve_alias_terminates_on_cycle():
    aliases = {("author", _name_key("A")): "B", ("author", _name_key("B")): "A"}
    result = resolve_alias(aliases, "author", "A")
    assert result in {"A", "B"}


def test_build_library_tree_merges_aliased_authors():
    books = [
        _book("b1", title="A", authors=["Brandon Sanderson"]),
        _book("b2", title="B", authors=["B. Sanderson"]),
    ]
    aliases = {("author", _name_key("B. Sanderson")): "Brandon Sanderson"}
    tree = build_library_tree(books, aliases=aliases)
    assert [a.name for a in tree.authors] == ["Brandon Sanderson"]
    merged = tree.authors[0]
    titles = [b.title for s in merged.series for b in s.books] + [b.title for b in merged.standalone]
    assert sorted(titles) == ["A", "B"]


def test_build_library_tree_renames_author():
    books = [_book("b1", title="A", authors=["brandon sanderson"])]
    aliases = {("author", _name_key("brandon sanderson")): "Brandon Sanderson"}
    tree = build_library_tree(books, aliases=aliases)
    assert [a.name for a in tree.authors] == ["Brandon Sanderson"]


def test_build_library_tree_aliases_series():
    books = [
        _book("b1", title="A", authors=["x"], series=[SeriesRef(name="Mistborn", sequence=1.0)]),
        _book(
            "b2",
            title="B",
            authors=["x"],
            series=[SeriesRef(name="Mistborn Era 1", sequence=2.0)],
        ),
    ]
    aliases = {("series", _name_key("Mistborn Era 1")): "Mistborn"}
    tree = build_library_tree(books, aliases=aliases)
    author = tree.authors[0]
    assert [s.name for s in author.series] == ["Mistborn"]
    assert sorted(b.title for s in author.series for b in s.books) == ["A", "B"]


def test_build_library_tree_aliases_franchise():
    b = _book("b1", title="A", authors=["x"])
    aliases = {("franchise", _name_key("cosmere")): "The Cosmere"}
    tree = build_library_tree([b], franchise_of={b.id: "cosmere"}, aliases=aliases)
    assert [f.name for f in tree.franchises] == ["The Cosmere"]
