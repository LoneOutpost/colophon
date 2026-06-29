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
