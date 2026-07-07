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


def test_fill_book_franchise_from_classified_ancestor(tmp_path):
    from colophon.core.graph import DirectoryNode
    from colophon.core.graph_records import fill_book_franchise

    scan = tmp_path / "scan"
    (scan / "Star Wars" / "A Book").mkdir(parents=True)
    (scan / "Star Wars" / "A Book" / "01.mp3").write_bytes(b"")

    import colophon.app_context as app_context
    from colophon.adapters.config import Config
    ctx = app_context.AppContext.create(
        Config(db_path=tmp_path / "db.sqlite", library_root=tmp_path / "lib", scan_paths=[scan])
    )
    g = build_graph(ctx.books, scan, template="$Author - $Title")
    book = next(bn.book for bn in g.books.values())
    d = g.directories[DirectoryNode.id_for(scan / "Star Wars")]
    d.kind = "franchise"
    d.kind_value = "Star Wars"

    assert fill_book_franchise(g, book, scan) is True
    assert book.franchise == "Star Wars"
    assert book.provenance.get("franchise") == "directory"

    book.franchise = "Manual Pick"
    book.provenance["franchise"] = "manual"
    assert fill_book_franchise(g, book, scan) is False
    assert book.franchise == "Manual Pick"
    ctx.close()


def test_resolve_book_franchise_precedence(tmp_path):
    from colophon.core.graph_records import resolve_book_franchise
    from colophon.core.models import BookUnit

    b = BookUnit.new(source_folder=tmp_path / "b")
    # Absent book franchise: folder value is used.
    assert resolve_book_franchise(b, "Folder Fr") == "Folder Fr"
    # Weak (folder-filled) book franchise yields to a fresh folder value (e.g. an override).
    b.franchise = "Stale"
    b.provenance["franchise"] = "directory"
    assert resolve_book_franchise(b, "Override Fr") == "Override Fr"
    # A strong (manual) book franchise wins over the folder value.
    b.franchise = "Manual Pick"
    b.provenance["franchise"] = "manual"
    assert resolve_book_franchise(b, "Folder Fr") == "Manual Pick"
    # No folder value: fall back to whatever the book has.
    assert resolve_book_franchise(b, None) == "Manual Pick"
