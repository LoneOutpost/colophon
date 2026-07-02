from pathlib import Path

from colophon.core.entity_graph import (
    EntityGraph,
    build_entity_graph,
    entity_graph_from_records,
)
from colophon.core.graph_records import (
    EdgeRecord,
    NodeRecord,
    book_node_id,
    entity_node_id,
)
from colophon.core.graph_resolve import _name_key
from colophon.core.library_graph import LibraryGraph
from colophon.core.models import BookUnit, SeriesRef


def _book(*, authors=None, series=None) -> BookUnit:
    b = BookUnit.new(source_folder=Path("/x"))
    b.title = "T"
    b.authors = authors or []
    b.series = series or []
    return b


def test_empty_input_empty_graph():
    g = build_entity_graph([])
    assert isinstance(g, EntityGraph)
    assert g.nodes == {} and g.members == {} and g.book_entities == {}


def test_author_node_and_membership_edge():
    b = _book(authors=["Brandon Sanderson"])
    g = build_entity_graph([b])
    ek = ("author", _name_key("Brandon Sanderson"))
    assert g.nodes[ek].name == "Brandon Sanderson"
    assert g.nodes[ek].kind == "author"
    assert g.members[ek] == [b]
    assert ek in g.book_entities[b.id]


def test_author_dedup_by_name_key_first_spelling_wins():
    b1 = _book(authors=["Brandon Sanderson"])
    b2 = _book(authors=["brandon  sanderson"])
    g = build_entity_graph([b1, b2])
    authors = [n for (k, _), n in g.nodes.items() if k == "author"]
    assert len(authors) == 1
    assert authors[0].name == "Brandon Sanderson"
    ek = ("author", _name_key("Brandon Sanderson"))
    assert g.members[ek] == [b1, b2]


def test_per_book_per_kind_dedup_links_once():
    b = _book(authors=["Alice", "alice"])
    g = build_entity_graph([b])
    ek = ("author", _name_key("Alice"))
    assert g.members[ek] == [b]
    assert g.book_entities[b.id].count(ek) == 1


def test_multi_membership_co_authored_book():
    b = _book(authors=["Alice", "Bob"])
    g = build_entity_graph([b])
    assert g.members[("author", _name_key("Alice"))] == [b]
    assert g.members[("author", _name_key("Bob"))] == [b]


def test_series_membership():
    b = _book(authors=["x"], series=[SeriesRef(name="Mistborn", sequence=1.0)])
    g = build_entity_graph([b])
    assert g.members[("series", _name_key("Mistborn"))] == [b]


def test_franchise_membership_from_franchise_of():
    b = _book(authors=["x"])
    g = build_entity_graph([b], franchise_of={b.id: "The Cosmere"})
    assert g.members[("franchise", _name_key("The Cosmere"))] == [b]


def test_alias_merges_two_author_spellings_to_one_node():
    b1 = _book(authors=["B. Sanderson"])
    b2 = _book(authors=["Brandon Sanderson"])
    aliases = {("author", _name_key("B. Sanderson")): "Brandon Sanderson"}
    g = build_entity_graph([b1, b2], aliases=aliases)
    authors = [n for (k, _), n in g.nodes.items() if k == "author"]
    assert len(authors) == 1
    assert g.members[("author", _name_key("Brandon Sanderson"))] == [b1, b2]


def _book_node(book_id, root="/lib"):
    return NodeRecord(id=book_node_id(book_id), physical=None, semantic="book",
                      root=root, attrs={"book_id": book_id})


def _entity_node(kind, name, root="/lib"):
    return NodeRecord(id=entity_node_id(kind, name, Path(root)), physical=None, semantic=kind,
                      root=root, attrs={"name": name, "name_key": _name_key(name)})


def _author_edge(book_id, name, root="/lib"):
    return EdgeRecord(src=book_node_id(book_id), kind="author",
                      dst=entity_node_id("author", name, Path(root)), root=root, props={})


def test_from_records_builds_author_membership():
    b = _book(authors=["Brandon Sanderson"])
    lg = LibraryGraph.from_records(
        [_book_node(b.id), _entity_node("author", "Brandon Sanderson")],
        [_author_edge(b.id, "Brandon Sanderson")],
    )
    g = entity_graph_from_records(lg, {b.id: b})
    ek = ("author", _name_key("Brandon Sanderson"))
    assert g.nodes[ek].name == "Brandon Sanderson"
    assert g.members[ek] == [b]
    assert ek in g.book_entities[b.id]
    assert g.books == [b]


def test_from_records_applies_aliases_at_read_time():
    b = _book(authors=["B. Sanderson"])
    lg = LibraryGraph.from_records(
        [_book_node(b.id), _entity_node("author", "B. Sanderson")],
        [_author_edge(b.id, "B. Sanderson")],
    )
    aliases = {("author", _name_key("B. Sanderson")): "Brandon Sanderson"}
    g = entity_graph_from_records(lg, {b.id: b}, aliases=aliases)
    ek = ("author", _name_key("Brandon Sanderson"))
    assert ek in g.nodes and g.nodes[ek].name == "Brandon Sanderson"


def test_from_records_skips_book_node_without_bookunit():
    lg = LibraryGraph.from_records(
        [_book_node("ghost"), _entity_node("author", "X")],
        [_author_edge("ghost", "X")],
    )
    g = entity_graph_from_records(lg, {})
    assert g.nodes == {} and g.members == {} and g.book_entities == {} and g.books == []


def test_from_records_empty_graph():
    g = entity_graph_from_records(LibraryGraph.from_records([], []), {})
    assert g.nodes == {} and g.books == []


def test_canonical_display_prefers_authoritative_source():
    from colophon.core.entity_graph import _canonical_display
    # tag spelling beats a folder-derived one
    assert _canonical_display([("Robert A Heinlein", "directory"),
                               ("Robert A. Heinlein", "tag")]) == "Robert A. Heinlein"
    # a match source beats a tag
    assert _canonical_display([("Robert A. Heinlein", "tag"),
                               ("Robert Anson Heinlein", "audnexus")]) == "Robert Anson Heinlein"
    # equal authority -> most frequent, then first-seen
    assert _canonical_display([("A B", "tag"), ("A. B.", "tag"), ("A B", "tag")]) == "A B"
    assert _canonical_display([("A B", "tag"), ("A. B.", "tag")]) == "A B"   # tie -> first-seen
    # a lone weak spelling is still used; empty -> ""
    assert _canonical_display([("Sean Flynn", "directory")]) == "Sean Flynn"
    assert _canonical_display([]) == ""
