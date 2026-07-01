from colophon.core.graph_explore import (
    Subgraph,
    display_kind,
    neighborhood,
    search_nodes,
    to_echart,
)
from colophon.core.graph_records import EdgeRecord, NodeRecord
from colophon.core.library_graph import LibraryGraph


def _n(id, *, semantic=None, physical=None, name=None, book_id=None) -> NodeRecord:
    attrs = {}
    if name:
        attrs["name"] = name
    if book_id:
        attrs["book_id"] = book_id
    return NodeRecord(id=id, physical=physical, semantic=semantic, root="/lib", attrs=attrs)


def _e(src, kind, dst) -> EdgeRecord:
    return EdgeRecord(src=src, kind=kind, dst=dst, root="/lib")


def _graph():
    nodes = [
        _n("root", physical="directory", name="TE_Audiobooks_S"),
        _n("A", semantic="author", name="Stella Rimington"),
        _n("S", semantic="series", name="Liz Carlyle"),
        _n("b1", semantic="book", book_id="bid1"),
        _n("b2", semantic="book", book_id="bid2"),
        _n("b3", semantic="book", book_id="bid3"),
        _n("far", physical="directory", name="unrelated"),
    ]
    edges = [
        _e("root", "contains", "A"),
        _e("A", "contains", "b1"), _e("A", "contains", "b2"), _e("A", "contains", "b3"),
        _e("S", "series", "b1"), _e("S", "series", "b2"),
    ]
    return LibraryGraph.from_records(nodes, edges)


def test_display_kind():
    assert display_kind(_n("x", semantic="author")) == "author"
    assert display_kind(_n("x", semantic="book", book_id="b")) == "book"
    assert display_kind(_n("x", physical="file", name="f.mp3")) == "file"
    assert display_kind(_n("x", physical="directory", name="d")) == "folder"


def test_neighborhood_one_hop():
    sub = neighborhood(_graph(), "A", hops=1)
    assert sub.node_ids[0] == "A"
    assert set(sub.node_ids) == {"A", "root", "b1", "b2", "b3"}
    assert "far" not in sub.node_ids and "S" not in sub.node_ids
    assert sub.omitted == 0
    assert all(e.src in sub.node_ids and e.dst in sub.node_ids for e in sub.edges)


def test_neighborhood_budget_caps_and_reports_omitted():
    sub = neighborhood(_graph(), "A", hops=1, budget=3)
    assert len(sub.node_ids) == 3
    assert sub.omitted == 2


def test_neighborhood_missing_focal_is_empty():
    sub = neighborhood(_graph(), "nope", hops=1)
    assert sub == Subgraph(node_ids=[], edges=[], omitted=0)


def test_search_ranks_semantic_before_directories():
    g = _graph()
    hits = search_nodes(g, "Stella", name_of=lambda n: n.attrs.get("name", ""))
    assert hits == ["A"]
    ranked = search_nodes(g, "e", name_of=lambda n: n.attrs.get("name", ""))
    assert ranked.index("A") < ranked.index("root")


def test_kind_constants_are_consistent():
    from colophon.core.graph_explore import KIND_COLOR, KIND_ICON, KINDS

    assert KINDS == ("author", "series", "franchise", "book", "folder", "file")
    assert set(KIND_COLOR) == set(KINDS)
    assert set(KIND_ICON) == set(KINDS)
    assert KIND_ICON == {
        "author": "person", "series": "layers", "franchise": "collections_bookmark",
        "book": "menu_book", "folder": "folder", "file": "description",
    }


def test_kind_symbols_are_echart_paths():
    from colophon.core.graph_explore import KINDS, _KIND_SYMBOL

    assert set(_KIND_SYMBOL) == set(KINDS)
    assert all(v.startswith("path://") for v in _KIND_SYMBOL.values())


def test_to_echart_structure():
    g = _graph()
    sub = neighborhood(g, "A", hops=1)
    opts = to_echart(g, sub, "A",
                     label_of=lambda n: n.attrs.get("name", n.id),
                     confidence_of=lambda n: None)
    series = opts["series"][0]
    assert series["type"] == "graph"
    assert len(series["data"]) == len(sub.node_ids)
    assert len(series["links"]) == len(sub.edges)
    assert len(series["categories"]) == 6
    focal = next(d for d in series["data"] if d["id"] == "A")
    other = next(d for d in series["data"] if d["id"] == "b1")
    assert focal["symbolSize"] > other["symbolSize"]
