"""Manual franchise assignment flows through the maintained graph."""

from colophon.adapters.config import Config
from colophon.app_context import AppContext
from colophon.controller import AppController
from colophon.core.graph_records import book_node_id, graph_records
from colophon.services.graph_build import build_graph


def _ctx(tmp_path):
    scan = tmp_path / "scan"
    (scan / "Some Author" / "A Book").mkdir(parents=True)
    (scan / "Some Author" / "A Book" / "01.mp3").write_bytes(b"")
    ctx = AppContext.create(
        Config(db_path=tmp_path / "db.sqlite", library_root=tmp_path / "lib", scan_paths=[scan])
    )
    g = build_graph(ctx.books, scan, template="$Author - $Title")
    books = [bn.book for bn in g.books.values()]
    for b in books:
        ctx.books.upsert(b)
    ctx.library_graph.replace_root(str(scan), *graph_records(g, books, root=scan))
    return ctx, scan, books[0]


def test_resync_derives_franchise_edge_from_book_field(tmp_path):
    ctx, scan, book = _ctx(tmp_path)
    book.franchise = "Star Wars"
    ctx.books.upsert(book)

    AppController(ctx)._resync_roots({scan})

    fr = [e for e in ctx.library_graph.edges
          if e.kind == "franchise" and e.src == book_node_id(book.id)]
    assert len(fr) == 1
    ctx.close()
