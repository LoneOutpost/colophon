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


def test_known_franchises_unions_declared_builtin_and_present(tmp_path):
    ctx, _scan, book = _ctx(tmp_path)
    ctrl = AppController(ctx)
    ctrl.add_franchise("Declared One")
    book.franchise = "In Library"
    ctx.books.upsert(book)

    names = ctrl.known_franchises()
    assert "Declared One" in names
    assert "In Library" in names
    assert names == sorted(names, key=str.casefold)
    assert set(ctrl.builtin_franchises()).issubset(set(names))
    ctx.close()


def test_resync_fills_franchise_from_declared_folder(tmp_path):
    scan = tmp_path / "scan"
    (scan / "Star Wars" / "A Book").mkdir(parents=True)
    (scan / "Star Wars" / "A Book" / "01.mp3").write_bytes(b"")
    ctx = AppContext.create(
        Config(db_path=tmp_path / "db.sqlite", library_root=tmp_path / "lib", scan_paths=[scan])
    )
    from colophon.core.graph_records import graph_records
    ctrl = AppController(ctx)
    ctrl.add_franchise("Star Wars")   # declare so the classifier tags the folder

    g = build_graph(ctx.books, scan, template="$Author - $Title")
    books = [bn.book for bn in g.books.values()]
    for b in books:
        ctx.books.upsert(b)
    ctx.library_graph.replace_root(str(scan), *graph_records(g, books, root=scan))

    ctrl._resync_roots({scan})

    stored = ctx.books.get(books[0].id)
    assert stored.franchise == "Star Wars"
    # The graph edge must exist after a SINGLE resync (not lag a pass behind the field), so the
    # franchise navigator and the detail view agree.
    fr = [e for e in ctx.library_graph.edges
          if e.kind == "franchise" and e.src == book_node_id(books[0].id)]
    assert len(fr) == 1
    ctx.close()
