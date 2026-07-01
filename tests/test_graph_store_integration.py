from pathlib import Path

from colophon.adapters.config import Config
from colophon.app_context import AppContext
from colophon.controller import AppController
from colophon.core.graph import DirectoryNode, FileNode


def _ctx(tmp_path) -> AppContext:
    return AppContext.create(Config(db_path=tmp_path / "db.sqlite", library_root=tmp_path / "lib"))


def _seed(root: Path) -> None:
    book = root / "Author" / "Dune"
    book.mkdir(parents=True)
    (book / "01.mp3").write_bytes(b"\x00" * 16)
    (book / "02.mp3").write_bytes(b"\x00" * 16)


def test_full_scan_persists_structural_graph(tmp_path):
    root = tmp_path / "lib"
    _seed(root)
    ctx = _ctx(tmp_path)
    ctx.config.scan_paths = [root]
    ctrl = AppController(ctx)
    ctrl.apply_scan(ctrl.scan_preview([root]))

    nodes = ctx.graph.nodes_for(root)
    by_id = {n.id: n for n in nodes}
    book_dir = root / "Author" / "Dune"
    f1 = book_dir / "01.mp3"
    # directory + file nodes exist with the right facets
    assert by_id[DirectoryNode.id_for(book_dir)].physical == "directory"
    assert by_id[FileNode.id_for(f1)].physical == "file"
    # exactly one book node under the folder, joinable to the persisted unit via attrs
    book_nodes = [n for n in nodes if n.semantic == "book"]
    assert len(book_nodes) == 1
    bnode = book_nodes[0]
    assert ctx.books.get(bnode.attrs["book_id"]) is not None  # graph book ↔ persisted BookUnit

    triples = {(e.src, e.kind, e.dst) for e in ctx.graph.edges_for(root)}
    assert (DirectoryNode.id_for(book_dir), "contains", FileNode.id_for(f1)) in triples  # physical
    assert (bnode.id, "owns", FileNode.id_for(f1)) in triples                            # composition
    assert (DirectoryNode.id_for(book_dir), "contains", bnode.id) in triples             # dir holds book


def test_full_scan_persists_semantic_graph(tmp_path):
    from mutagen.id3 import ID3, TPE1

    root = tmp_path / "lib"
    author = root / "Brandon Sanderson"
    for title in ("Elantris", "Warbreaker"):
        d = author / title
        d.mkdir(parents=True)
        f = d / "01.mp3"
        f.write_bytes(b"\x00" * 16)
        tags = ID3()
        tags.add(TPE1(encoding=3, text=["Brandon Sanderson"]))
        tags.save(f)
    ctx = _ctx(tmp_path)
    ctx.config.scan_paths = [root]
    ctrl = AppController(ctx)
    ctrl.apply_scan(ctrl.scan_preview([root]))

    nodes = ctx.graph.nodes_for(root)
    # author entity nodes are logical-only (physical is None); the author *folder* also
    # carries a semantic="author" facet, so filter to the entity to test dedup.
    author_entities = [n for n in nodes if n.semantic == "author" and n.physical is None]
    assert len(author_entities) == 1  # one shared "Brandon Sanderson" entity
    eid = author_entities[0].id
    author_edges = [e for e in ctx.graph.edges_for(root) if e.kind == "author"]
    assert len(author_edges) == 2 and all(e.dst == eid for e in author_edges)  # both books -> it


def test_rescan_after_removing_a_file_updates_the_store(tmp_path):
    root = tmp_path / "lib"
    _seed(root)
    ctx = _ctx(tmp_path)
    ctx.config.scan_paths = [root]
    ctrl = AppController(ctx)
    ctrl.apply_scan(ctrl.scan_preview([root]))
    removed = root / "Author" / "Dune" / "02.mp3"
    assert any(n.id == FileNode.id_for(removed) for n in ctx.graph.nodes_for(root))

    removed.unlink()
    ctrl.apply_scan(ctrl.scan_preview([root]))
    assert not any(n.id == FileNode.id_for(removed) for n in ctx.graph.nodes_for(root))  # pruned


def test_two_roots_persist_and_replace_independently(tmp_path):
    r1, r2 = tmp_path / "lib1", tmp_path / "lib2"
    _seed(r1)
    _seed(r2)
    ctx = _ctx(tmp_path)
    ctx.config.scan_paths = [r1, r2]
    ctrl = AppController(ctx)
    ctrl.apply_scan(ctrl.scan_preview([r1, r2]))  # combined multi-root plan
    # each root's subgraph is persisted and scoped to itself
    assert ctx.graph.nodes_for(r1) and all(n.root == str(r1) for n in ctx.graph.nodes_for(r1))
    assert ctx.graph.nodes_for(r2) and all(n.root == str(r2) for n in ctx.graph.nodes_for(r2))

    # re-scanning r1 alone replaces only r1's subgraph; r2 is untouched
    r2_before = {n.id for n in ctx.graph.nodes_for(r2)}
    ctrl.apply_scan(ctrl.scan_preview([r1]))
    assert {n.id for n in ctx.graph.nodes_for(r2)} == r2_before


def test_graph_neighborhood_hidden_filters_kind(tmp_path):
    root = tmp_path / "lib"
    _seed(root)  # root/Author/Dune with two mp3s
    ctx = _ctx(tmp_path)
    ctx.config.scan_paths = [root]
    ctrl = AppController(ctx)
    ctrl.apply_scan(ctrl.scan_preview([root]))

    from colophon.core.graph_explore import _KIND_INDEX

    # Focus the book folder: its 1-hop neighborhood includes the two file nodes.
    book_dir = DirectoryNode.id_for(root / "Author" / "Dune")
    file_cat = _KIND_INDEX["file"]

    full = ctrl.graph_neighborhood(book_dir)
    full_data = full["echart"]["series"][0]["data"]
    assert any(d["category"] == file_cat for d in full_data)  # files present unfiltered

    hidden = ctrl.graph_neighborhood(book_dir, hidden=frozenset({"file"}))
    hidden_data = hidden["echart"]["series"][0]["data"]
    assert all(d["category"] != file_cat for d in hidden_data)  # files gone when hidden
    assert hidden_data  # the focal folder itself is still shown


def test_controller_graph_inspect_and_depth(tmp_path):
    root = tmp_path / "lib"
    _seed(root)  # root/Author/Dune
    ctx = _ctx(tmp_path)
    ctx.config.scan_paths = [root]
    ctrl = AppController(ctx)
    ctrl.apply_scan(ctrl.scan_preview([root]))

    author_dir = DirectoryNode.id_for(root / "Author")
    got = ctrl.graph_inspect(author_dir)
    # a physical directory is always framed structurally (a "Contains" row), regardless of whether
    # the scan classified it as an author (which would make display-kind "author").
    assert any(label == "Contains" for label, _ in got.rows)

    view1 = ctrl.graph_neighborhood(author_dir, hops=1)
    assert set(view1) == {"echart", "omitted"}
    view2 = ctrl.graph_neighborhood(author_dir, hops=2)
    assert len(view2["echart"]["series"][0]["data"]) >= len(view1["echart"]["series"][0]["data"])
