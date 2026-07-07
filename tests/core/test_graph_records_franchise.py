"""The graph franchise edge derives from book.franchise (folder derivation is the fallback)."""

from colophon.core.graph_records import book_node_id, graph_records
from colophon.services.graph_build import build_graph


def test_franchise_edge_prefers_book_field(tmp_path):
    scan = tmp_path / "scan"
    (scan / "Some Author" / "A Book").mkdir(parents=True)
    (scan / "Some Author" / "A Book" / "01.mp3").write_bytes(b"")

    import colophon.app_context as app_context
    from colophon.adapters.config import Config
    ctx = app_context.AppContext.create(
        Config(db_path=tmp_path / "db.sqlite", library_root=tmp_path / "lib", scan_paths=[scan])
    )
    g = build_graph(ctx.books, scan, template="$Author - $Title")
    book = next(bn.book for bn in g.books.values())
    book.franchise = "Star Wars"                 # no folder is classified franchise here

    nodes, edges = graph_records(g, [book], root=scan)
    fr_edges = [e for e in edges if e.kind == "franchise" and e.src == book_node_id(book.id)]
    assert len(fr_edges) == 1
    dst_names = {n.id: n.attrs.get("name", "") for n in nodes}
    assert any("star" in dst_names.get(e.dst, "").lower() for e in fr_edges)
    ctx.close()
