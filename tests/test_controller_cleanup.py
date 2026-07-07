"""Controller-level tests for the Utilities clean-up action."""

from colophon.adapters.config import Config
from colophon.app_context import AppContext
from colophon.controller import AppController
from colophon.core.graph_records import book_node_id, graph_records
from colophon.core.models import BookUnit, EditChange
from colophon.services.graph_build import build_graph


def _ctx(tmp_path, scan_paths):
    return AppContext.create(
        Config(
            db_path=tmp_path / "db.sqlite",
            library_root=tmp_path / "lib",
            scan_paths=scan_paths,
        )
    )


def test_cleanup_report_buckets_persisted_books(tmp_path):
    scan = tmp_path / "scan"
    scan.mkdir()
    ctx = _ctx(tmp_path, [scan])
    gone = BookUnit.new(source_folder=scan / "Gone")            # under scan, missing
    outside = BookUnit.new(source_folder=tmp_path / "away" / "X")  # outside scan
    ctx.books.upsert(gone)
    ctx.books.upsert(outside)

    report = AppController(ctx).cleanup_report()

    assert {c.book_id for c in report.removed_from_disk} == {gone.id}
    assert {c.book_id for c in report.outside_scan_paths} == {outside.id}
    ctx.close()


def test_cleanup_remove_deletes_records_and_prunes_graph(tmp_path):
    scan = tmp_path / "scan"
    (scan / "Frank Herbert" / "Dune").mkdir(parents=True)
    (scan / "Frank Herbert" / "Dune" / "01.mp3").write_bytes(b"")
    ctx = _ctx(tmp_path, [scan])

    # Seed as a scan would: books persisted + graph records written through.
    g = build_graph(ctx.books, scan, template="$Author - $Title")
    books = [bn.book for bn in g.books.values()]
    for b in books:
        ctx.books.upsert(b)
    ctx.library_graph.replace_root(str(scan), *graph_records(g, books, root=scan))
    victim = books[0]

    # Give the victim an edit-history row to prove satellite rows are cleaned too.
    ctx.history.record("batch-1", [EditChange(book_id=victim.id, field="title",
                                              old_value="a", new_value="b")])
    assert any(n.attrs.get("book_id") == victim.id
               for n in ctx.library_graph.nodes.values())

    n = AppController(ctx).cleanup_remove([victim.id])

    assert n == 1
    assert ctx.books.get(victim.id) is None
    assert ctx.history.list_batch("batch-1") == []
    assert book_node_id(victim.id) not in ctx.library_graph.nodes
    dangling = [e for e in ctx.library_graph.edges
                if book_node_id(victim.id) in (e.src, e.dst)]
    assert dangling == []
    ctx.close()


def test_cleanup_remove_empty_is_noop(tmp_path):
    ctx = _ctx(tmp_path, [tmp_path / "scan"])
    assert AppController(ctx).cleanup_remove([]) == 0
    ctx.close()
