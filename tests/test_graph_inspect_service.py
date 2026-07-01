from pathlib import Path

from colophon.adapters.config import Config
from colophon.app_context import AppContext
from colophon.controller import AppController
from colophon.core.graph import DirectoryNode


def _ctx(tmp_path) -> AppContext:
    return AppContext.create(Config(db_path=tmp_path / "db.sqlite", library_root=tmp_path / "lib"))


def _seed(root: Path) -> None:
    book = root / "Brandon Sanderson" / "Elantris"
    book.mkdir(parents=True)
    (book / "01.mp3").write_bytes(b"\x00" * 16)


def _scanned(tmp_path):
    root = tmp_path / "lib"
    _seed(root)
    ctx = _ctx(tmp_path)
    ctx.config.scan_paths = [root]
    ctrl = AppController(ctx)
    ctrl.apply_scan(ctrl.scan_preview([root]))
    return ctx, ctrl, root


def test_inspect_book_returns_rows_and_links(tmp_path):
    from colophon.services import graph_inspect

    ctx, _ctrl, root = _scanned(tmp_path)
    book_dir = DirectoryNode.id_for(root / "Brandon Sanderson" / "Elantris")
    book_ids = [e.dst for e in ctx.library_graph.edges
                if e.src == book_dir and e.kind == "contains"
                and ctx.library_graph.nodes[e.dst].semantic == "book"]
    assert book_ids
    got = graph_inspect.inspect(ctx.library_graph, ctx.books, book_ids[0])
    assert got.kind == "book"
    assert any(label == "Files" for label, _ in got.rows)
    assert any(link.url.startswith("/?filter=") for link in got.links)


def test_neighborhood_view_depth_widens(tmp_path):
    from colophon.services import graph_inspect

    ctx, _ctrl, root = _scanned(tmp_path)
    author_dir = DirectoryNode.id_for(root / "Brandon Sanderson")
    d1 = graph_inspect.neighborhood_view(ctx.library_graph, ctx.books, author_dir, depth=1, hidden=frozenset())
    d2 = graph_inspect.neighborhood_view(ctx.library_graph, ctx.books, author_dir, depth=2, hidden=frozenset())
    assert len(d2["echart"]["series"][0]["data"]) >= len(d1["echart"]["series"][0]["data"])
    assert "omitted" in d1


def test_inspect_missing_focal_is_empty(tmp_path):
    from colophon.services import graph_inspect

    ctx, _ctrl, _root = _scanned(tmp_path)
    got = graph_inspect.inspect(ctx.library_graph, ctx.books, "nope")
    assert got.kind == ""
