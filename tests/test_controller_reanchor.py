from colophon.controller import AppController
from colophon.core.models import (
    BookUnit,
    EditChange,
    OperationRecord,
    Phase,
    PhaseState,
    SourceFile,
)
from colophon.core.phases import mark, resync_state
from tests.test_controller import _ctx


def _organized_book(ctx, tmp_path, *, name="Elantris", author="Brandon Sanderson"):
    """A book already organized: source in a scan root, output M4B on disk, ORGANIZE fresh."""
    ingest = tmp_path / "ingest"
    ctx.config.scan_paths = [ingest]
    src = ingest / author / name
    src.mkdir(parents=True)
    (src / f"{name}.mp3").write_bytes(b"")
    lib = tmp_path / "library" / author / name
    lib.mkdir(parents=True)
    out = lib / f"{name}.m4b"
    out.write_bytes(b"\x00" * 32)
    book = BookUnit.new(source_folder=src)
    book.title = name
    book.authors = [author]
    book.source_files = [SourceFile(path=src / f"{name}.mp3", size=1, duration_seconds=60.0, ext="mp3")]
    book.output_path = out
    mark(book, Phase.ORGANIZE, PhaseState.FRESH)
    resync_state(book)
    ctx.books.upsert(book)
    return book, out


def test_reanchor_moves_identity_to_output(tmp_path):
    ctx = _ctx(tmp_path)
    ctrl = AppController(ctx)
    book, out = _organized_book(ctx, tmp_path)
    old_id = book.id
    result = ctrl._reanchor_after_organize(book)
    assert result is not None
    new_id = BookUnit.id_for(out)
    assert new_id != old_id
    assert book.id == new_id
    assert book.source_folder == out.parent
    assert [sf.path for sf in book.source_files] == [out]
    assert ctx.books.get(old_id) is None
    assert ctx.books.get(new_id) is not None


def test_reanchor_migrates_history_and_operations(tmp_path):
    ctx = _ctx(tmp_path)
    ctrl = AppController(ctx)
    book, _out = _organized_book(ctx, tmp_path)
    old_id = book.id
    ctx.history.record("b1", [EditChange(book_id=old_id, field="title", old_value="a", new_value="b")])
    ctx.operations.record(OperationRecord(batch_id="b2", book_id=old_id, op_type="x", target="t", outcome="ok"))
    ctrl._reanchor_after_organize(book)
    assert ctx.history.list_batch("b1")[0].book_id == book.id
    assert ctx.operations.list_batch("b2")[0].book_id == book.id


def test_reanchor_migrates_cover_file(tmp_path):
    ctx = _ctx(tmp_path)
    ctrl = AppController(ctx)
    book, out = _organized_book(ctx, tmp_path)
    cov = book.source_folder / f"cover-{book.id}.jpg"
    cov.write_bytes(b"img")
    book.cover_path = cov
    ctx.books.upsert(book)
    ctrl._reanchor_after_organize(book)
    assert book.cover_path == out.parent / f"cover-{book.id}.jpg"
    assert book.cover_path.exists()
    assert not cov.exists()


def test_reanchor_is_idempotent(tmp_path):
    ctx = _ctx(tmp_path)
    ctrl = AppController(ctx)
    book, _out = _organized_book(ctx, tmp_path)
    assert ctrl._reanchor_after_organize(book) is not None
    assert ctrl._reanchor_after_organize(book) is None


def test_reanchor_skips_on_id_collision(tmp_path):
    ctx = _ctx(tmp_path)
    ctrl = AppController(ctx)
    book, out = _organized_book(ctx, tmp_path)
    old_id = book.id
    squatter = BookUnit.new(source_folder=out)  # id == id_for(out)
    ctx.books.upsert(squatter)
    assert ctrl._reanchor_after_organize(book) is None
    assert book.id == old_id


def test_reanchor_preserves_manual_and_confidence(tmp_path):
    ctx = _ctx(tmp_path)
    ctrl = AppController(ctx)
    book, _out = _organized_book(ctx, tmp_path)
    book.manually_confirmed = True
    book.confidence = 100.0
    book.title = "Manual Title"
    ctx.books.upsert(book)
    ctrl._reanchor_after_organize(book)
    stored = ctx.books.get(book.id)
    assert stored.manually_confirmed is True
    assert stored.confidence == 100.0
    assert stored.title == "Manual Title"


def test_reanchor_organized_keeps_author_grouping(tmp_path):
    ctx = _ctx(tmp_path)
    ctrl = AppController(ctx)
    book, _out = _organized_book(ctx, tmp_path, author="Brandon Sanderson", name="Elantris")
    ctrl._reanchor_organized([book])
    tree = ctrl.library_tree()
    author = next((a for a in tree.authors if a.name == "Brandon Sanderson"), None)
    assert author is not None
    grouped = {b.id for b in author.standalone} | {b.id for s in author.series for b in s.books}
    assert book.id in grouped


def test_reanchor_organized_still_in_organize_phase_view(tmp_path):
    ctx = _ctx(tmp_path)
    ctrl = AppController(ctx)
    book, _out = _organized_book(ctx, tmp_path)
    ctrl._reanchor_organized([book])
    assert book.id in {b.id for b in ctrl.books_with_phase(Phase.ORGANIZE, PhaseState.FRESH)}


def test_reanchor_organized_survives_reconcile(tmp_path):
    ctx = _ctx(tmp_path)
    ctrl = AppController(ctx)
    book, _out = _organized_book(ctx, tmp_path, author="Brandon Sanderson", name="Elantris")
    ctrl._reanchor_organized([book])
    ctrl.reconcile_graph()   # active_roots = scan_paths; output root is NOT among them
    tree = ctrl.library_tree()
    assert "Brandon Sanderson" in {a.name for a in tree.authors}


def test_reanchor_organized_skips_unorganized(tmp_path):
    ctx = _ctx(tmp_path)
    ctrl = AppController(ctx)
    ctx.config.scan_paths = [tmp_path / "ingest"]
    book = BookUnit.new(source_folder=tmp_path / "ingest" / "x")
    ctx.books.upsert(book)
    old_id = book.id
    ctrl._reanchor_organized([book])   # no output_path -> skipped, no crash
    assert book.id == old_id
