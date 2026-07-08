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
    # An identified title folder gets its own bucket, not the generic 'folder'.
    assert display_kind(_n("x", semantic="title")) == "title"
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

    assert KINDS == ("author", "series", "franchise", "book", "title", "folder", "file")
    assert set(KIND_COLOR) == set(KINDS)
    assert set(KIND_ICON) == set(KINDS)
    assert KIND_ICON == {
        "author": "person", "series": "layers", "franchise": "collections_bookmark",
        "book": "menu_book", "title": "folder_special", "folder": "folder", "file": "description",
    }


def test_kind_symbols_are_echart_paths():
    from colophon.core.graph_explore import _KIND_SYMBOL, KINDS

    assert set(_KIND_SYMBOL) == set(KINDS)
    assert all(v.startswith("path://") for v in _KIND_SYMBOL.values())


def test_to_echart_structure():
    from colophon.core.graph_explore import KINDS

    g = _graph()
    sub = neighborhood(g, "A", hops=1)
    opts = to_echart(g, sub, "A",
                     label_of=lambda n: n.attrs.get("name", n.id),
                     confidence_of=lambda n: None)
    series = opts["series"][0]
    assert series["type"] == "graph"
    assert len(series["data"]) == len(sub.node_ids)
    assert len(series["links"]) == len(sub.edges)
    assert len(series["categories"]) == len(KINDS)
    focal = next(d for d in series["data"] if d["id"] == "A")
    other = next(d for d in series["data"] if d["id"] == "b1")
    assert focal["symbolSize"] > other["symbolSize"]


def test_to_echart_per_kind_symbols_and_no_legend():
    from colophon.core.graph_explore import _KIND_SYMBOL

    g = _graph()
    sub = neighborhood(g, "A", hops=1)
    opts = to_echart(g, sub, "A",
                     label_of=lambda n: n.attrs.get("name", n.id),
                     confidence_of=lambda n: None)
    assert "legend" not in opts
    data = opts["series"][0]["data"]
    assert all(d["symbol"].startswith("path://") for d in data)
    assert next(d for d in data if d["id"] == "A")["symbol"] == _KIND_SYMBOL["author"]
    assert next(d for d in data if d["id"] == "b1")["symbol"] == _KIND_SYMBOL["book"]


def test_to_echart_palette_border_and_label_halo():
    from colophon.core.graph_explore import KIND_COLOR

    g = _graph()
    sub = neighborhood(g, "A", hops=1)
    opts = to_echart(g, sub, "A",
                     label_of=lambda n: n.attrs.get("name", n.id),
                     confidence_of=lambda n: None)
    series = opts["series"][0]
    cats = {c["name"]: c["itemStyle"]["color"] for c in series["categories"]}
    assert cats["author"] == KIND_COLOR["author"] == "#c15a38"
    assert cats["book"] == "#2e8f80"
    focal = next(d for d in series["data"] if d["id"] == "A")
    other = next(d for d in series["data"] if d["id"] == "b1")
    assert other["itemStyle"]["borderWidth"] == 1
    assert focal["itemStyle"]["borderWidth"] == 3
    assert other["itemStyle"]["color"] == KIND_COLOR["book"]
    assert focal["itemStyle"]["color"] == KIND_COLOR["author"]
    assert "borderColor" in other["itemStyle"]
    label = series["label"]
    assert label["textBorderWidth"] >= 2
    assert "textBorderColor" in label


def test_to_echart_hidden_drops_kind_but_keeps_focal():
    g = _graph()
    sub = neighborhood(g, "A", hops=1)  # {A(author), root(folder), b1, b2, b3(book)}
    opts = to_echart(g, sub, "A",
                     label_of=lambda n: n.attrs.get("name", n.id),
                     confidence_of=lambda n: None,
                     hidden=frozenset({"book"}))
    ids = {d["id"] for d in opts["series"][0]["data"]}
    assert ids == {"A", "root"}
    for link in opts["series"][0]["links"]:
        assert link["source"] in ids and link["target"] in ids

    sub_b = neighborhood(g, "b1", hops=1)  # {b1(book), A(author), S(series)}
    opts_b = to_echart(g, sub_b, "b1",
                       label_of=lambda n: n.attrs.get("name", n.id),
                       confidence_of=lambda n: None,
                       hidden=frozenset({"book"}))
    ids_b = {d["id"] for d in opts_b["series"][0]["data"]}
    assert "b1" in ids_b


def test_filter_bucket_is_physical_first_for_directories():
    from colophon.core.graph_explore import filter_bucket
    assert filter_bucket(_n("x", physical="directory", semantic="author")) == "folder"
    assert filter_bucket(_n("x", physical="directory", semantic="title")) == "folder"
    assert filter_bucket(_n("x", physical="directory")) == "folder"
    assert filter_bucket(_n("x", semantic="author")) == "author"
    assert filter_bucket(_n("x", semantic="book", book_id="b")) == "book"
    assert filter_bucket(_n("x", physical="file", name="f.mp3")) == "file"


def test_type_label_suffixes_classified_folders():
    from colophon.core.graph_explore import type_label
    assert type_label(_n("x", physical="directory", semantic="author")) == "Author Folder"
    assert type_label(_n("x", physical="directory", semantic="series")) == "Series Folder"
    assert type_label(_n("x", physical="directory", semantic="franchise")) == "Franchise Folder"
    assert type_label(_n("x", physical="directory", semantic="title")) == "Title Folder"
    assert type_label(_n("x", physical="directory")) == "Folder"
    assert type_label(_n("x", semantic="author")) == "Author"
    assert type_label(_n("x", physical="file", name="f.mp3")) == "File"


def test_node_tint_keeps_classification_color_for_folders():
    from colophon.core.graph_explore import KIND_COLOR, node_tint
    assert node_tint(_n("x", physical="directory", semantic="author")) == KIND_COLOR["author"]
    assert node_tint(_n("x", physical="directory")) == KIND_COLOR["folder"]
    assert node_tint(_n("x", semantic="series")) == KIND_COLOR["series"]
    assert node_tint(_n("x", physical="file", name="f.mp3")) == KIND_COLOR["file"]


def test_node_glyph_uses_folder_family_for_classified_dirs():
    from colophon.core.graph_explore import _KIND_SYMBOL, node_glyph
    author_folder = node_glyph(_n("x", physical="directory", semantic="author"))
    assert author_folder.startswith("path://")
    assert author_folder != _KIND_SYMBOL["author"]
    assert author_folder != _KIND_SYMBOL["folder"]
    series_folder = node_glyph(_n("x", physical="directory", semantic="series"))
    assert series_folder.startswith("path://") and series_folder != _KIND_SYMBOL["series"]
    franchise_folder = node_glyph(_n("x", physical="directory", semantic="franchise"))
    assert franchise_folder.startswith("path://") and franchise_folder != _KIND_SYMBOL["franchise"]
    assert node_glyph(_n("x", physical="directory", semantic="title")) == _KIND_SYMBOL["title"]
    assert node_glyph(_n("x", physical="directory")) == _KIND_SYMBOL["folder"]


def _graph_with_author_folder():
    nodes = [
        _n("root", physical="directory", name="lib"),
        _n("af", physical="directory", semantic="author", name="CLIVE BARKER"),
    ]
    edges = [_e("root", "contains", "af")]
    return LibraryGraph.from_records(nodes, edges)


def test_to_echart_folder_classified_node_uses_folder_glyph_and_classification_tint():
    from colophon.core.graph_explore import (
        _FOLDER_KIND_SYMBOL,
        KIND_COLOR,
        neighborhood,
        to_echart,
    )
    g = _graph_with_author_folder()
    sub = neighborhood(g, "af", hops=1)
    opt = to_echart(g, sub, "af",
                    label_of=lambda n: n.attrs.get("name", ""),
                    confidence_of=lambda n: None)
    node = next(d for d in opt["series"][0]["data"] if d["id"] == "af")
    assert node["symbol"] == _FOLDER_KIND_SYMBOL["author"]
    assert node["itemStyle"]["color"] == KIND_COLOR["author"]


def test_to_echart_classified_folder_sized_like_its_entity():
    from colophon.core.graph_explore import neighborhood, to_echart
    g = _graph_with_author_folder()
    sub = neighborhood(g, "root", hops=1)
    opt = to_echart(g, sub, "root",
                    label_of=lambda n: n.attrs.get("name", ""),
                    confidence_of=lambda n: None)
    af = next(d for d in opt["series"][0]["data"] if d["id"] == "af")
    # author folder sized like an author entity (20), not shrunk to plain-folder size (14)
    assert af["symbolSize"] == 20


def test_to_echart_folder_filter_hides_classified_folders():
    from colophon.core.graph_explore import neighborhood, to_echart
    g = _graph_with_author_folder()
    sub = neighborhood(g, "root", hops=1)
    opt = to_echart(g, sub, "root",
                    label_of=lambda n: n.attrs.get("name", ""),
                    confidence_of=lambda n: None,
                    hidden=frozenset({"folder"}))
    ids = {d["id"] for d in opt["series"][0]["data"]}
    assert "af" not in ids
    assert ids == {"root"}
