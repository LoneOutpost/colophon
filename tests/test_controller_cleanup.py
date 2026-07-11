"""Controller-level tests for the Utilities clean-up action."""

from colophon.adapters.config import Config
from colophon.app_context import AppContext
from colophon.controller import AppController
from colophon.core.graph import leaf_id_for
from colophon.core.graph_records import book_node_id, graph_records
from colophon.core.models import BookUnit, EditChange, SourceFile
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


def test_cleanup_remove_multiple_books_flushes_whole_batch(tmp_path):
    # Two books removed in one call: the intermediate commit=False deletes must not be
    # lost, so both books and both their history rows are gone after the single final commit.
    scan = tmp_path / "scan"
    for title in ["Dune", "Hyperion"]:
        (scan / "Author" / title).mkdir(parents=True)
        (scan / "Author" / title / "01.mp3").write_bytes(b"")
    ctx = _ctx(tmp_path, [scan])

    g = build_graph(ctx.books, scan, template="$Author - $Title")
    books = [bn.book for bn in g.books.values()]
    for b in books:
        ctx.books.upsert(b)
    ctx.library_graph.replace_root(str(scan), *graph_records(g, books, root=scan))
    assert len(books) == 2
    for i, b in enumerate(books):
        ctx.history.record(f"batch-{i}", [EditChange(book_id=b.id, field="title",
                                                     old_value="a", new_value="b")])

    n = AppController(ctx).cleanup_remove([b.id for b in books])

    assert n == 2
    for i, b in enumerate(books):
        assert ctx.books.get(b.id) is None
        assert ctx.history.list_batch(f"batch-{i}") == []
        assert book_node_id(b.id) not in ctx.library_graph.nodes
    ctx.close()


def test_cleanup_remove_empty_is_noop(tmp_path):
    ctx = _ctx(tmp_path, [tmp_path / "scan"])
    assert AppController(ctx).cleanup_remove([]) == 0
    ctx.close()


def test_cleanup_remove_keeps_files_on_disk(tmp_path):
    # Forgetting a present book must not touch its audio files.
    scan = tmp_path / "scan"
    audio = scan / "Frank Herbert" / "Dune" / "01.mp3"
    audio.parent.mkdir(parents=True)
    audio.write_bytes(b"data")
    ctx = _ctx(tmp_path, [scan])

    g = build_graph(ctx.books, scan, template="$Author - $Title")
    books = [bn.book for bn in g.books.values()]
    for b in books:
        ctx.books.upsert(b)
    ctx.library_graph.replace_root(str(scan), *graph_records(g, books, root=scan))
    victim = books[0]

    removed = AppController(ctx).cleanup_remove([victim.id])

    assert removed == 1
    assert ctx.books.get(victim.id) is None
    assert audio.exists()                       # files are left on disk
    assert audio.read_bytes() == b"data"
    ctx.close()


def test_cleanup_remove_spares_clustered_sibling(tmp_path):
    # Two books that share one source_folder (a multi-book directory): removing one
    # must leave the sibling's record and graph node intact. A real clustered leaf
    # owns a distinct subset of the folder's files, so each gets a distinct id via
    # leaf_id_for (two whole-folder BookUnit.new() would collapse to one id).
    scan = tmp_path / "scan"
    folder = scan / "Anthology"
    folder.mkdir(parents=True)
    file_a = folder / "book-one.mp3"
    file_b = folder / "book-two.mp3"
    file_a.write_bytes(b"")
    file_b.write_bytes(b"")
    ctx = _ctx(tmp_path, [scan])

    def _clustered(file, title):
        book = BookUnit.new(source_folder=folder)
        book.id = leaf_id_for(folder, [file])
        book.source_files = [SourceFile(path=file, size=0, duration_seconds=0.0, ext="mp3")]
        book.title = title
        return book

    a = _clustered(file_a, "Book One")
    b = _clustered(file_b, "Book Two")
    assert a.id != b.id
    ctx.books.upsert(a)
    ctx.books.upsert(b)
    ctx.library_graph.replace_root(
        str(scan),
        *graph_records(
            build_graph(ctx.books, scan, template="$Author - $Title"),
            [a, b],
            root=scan,
        ),
    )
    assert any(n.attrs.get("book_id") == b.id for n in ctx.library_graph.nodes.values())

    removed = AppController(ctx).cleanup_remove([a.id])

    assert removed == 1
    assert ctx.books.get(a.id) is None
    assert ctx.books.get(b.id) is not None                       # sibling record survives
    assert book_node_id(a.id) not in ctx.library_graph.nodes
    assert book_node_id(b.id) in ctx.library_graph.nodes         # sibling node survives
    ctx.close()
