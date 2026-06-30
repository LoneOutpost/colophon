from pathlib import Path

from colophon.core.entity_graph import EntityGraph, build_entity_graph
from colophon.core.graph_resolve import _name_key
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
