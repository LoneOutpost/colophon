from colophon.controller import AppController
from colophon.core.models import BookUnit, SourceFile
from tests.test_controller import _ctx


def _book(ctx, tmp_path, name="Dune", author="Frank Herbert"):
    ctx.config.scan_paths = [tmp_path / "ingest"]
    ctx.config.library_root = tmp_path / "library"
    src = tmp_path / "ingest" / author / name
    src.mkdir(parents=True)
    (src / f"{name}.mp3").write_bytes(b"")
    book = BookUnit.new(source_folder=src)
    book.title = name
    book.authors = [author]
    book.source_files = [SourceFile(path=src / f"{name}.mp3", size=1, duration_seconds=1.0, ext="mp3")]
    ctx.books.upsert(book)
    return book


def test_organize_preview_reports_target(tmp_path):
    ctx = _ctx(tmp_path)
    ctrl = AppController(ctx)
    book = _book(ctx, tmp_path)
    (row,) = ctrl.organize_preview([book])
    assert row.book_id == book.id
    assert row.title == "Dune"
    assert row.target == dict(ctrl.organize_targets([book]))[book.id]
    assert row.collision is False
    assert row.blocked is False


def test_organize_preview_flags_collision(tmp_path):
    ctx = _ctx(tmp_path)
    ctrl = AppController(ctx)
    book = _book(ctx, tmp_path)
    target = dict(ctrl.organize_targets([book]))[book.id]
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(b"already here")
    (row,) = ctrl.organize_preview([book])
    assert row.collision is True


def test_organize_preview_flags_blocked(tmp_path):
    ctx = _ctx(tmp_path)
    ctrl = AppController(ctx)
    book = _book(ctx, tmp_path)
    book.missing = True   # a missing book is a blocking error
    ctx.books.upsert(book)
    (row,) = ctrl.organize_preview([book])
    assert row.blocked is True
    assert not row.target.exists()


def test_organize_preview_reorg_shows_folder_and_folder_collision(tmp_path):
    # Without encode, a reorg copies the originals into the book folder (one or many), so the
    # preview shows that folder (not a fake single .m4b) and flags a folder that already holds content.
    ctx = _ctx(tmp_path)
    ctrl = AppController(ctx)
    book = _book(ctx, tmp_path)
    target = dict(ctrl.organize_targets([book]))[book.id]

    (row,) = ctrl.organize_preview([book], encode=False)
    assert row.target == target.parent          # destination folder, not a fake .m4b path
    assert row.collision is False               # folder doesn't exist yet

    target.parent.mkdir(parents=True, exist_ok=True)
    (target.parent / "existing.mp3").write_bytes(b"x")
    (row2,) = ctrl.organize_preview([book], encode=False)
    assert row2.collision is True               # a folder that already holds content collides


def test_remove_from_library_drops_record_keeps_output(tmp_path):
    ctx = _ctx(tmp_path)
    ctrl = AppController(ctx)
    ctx.config.scan_paths = [tmp_path / "ingest"]
    src = tmp_path / "ingest" / "Frank Herbert" / "Dune"
    src.mkdir(parents=True)
    (src / "Dune.mp3").write_bytes(b"")
    out = tmp_path / "library" / "Frank Herbert" / "Dune.m4b"
    out.parent.mkdir(parents=True)
    out.write_bytes(b"organized output")
    book = BookUnit.new(source_folder=src)
    book.title = "Dune"
    book.output_path = out
    book.source_files = [SourceFile(path=src / "Dune.mp3", size=1, duration_seconds=1.0, ext="mp3")]
    ctx.books.upsert(book)

    n = ctrl.remove_from_library([book.id])
    assert n == 1
    assert ctx.books.get(book.id) is None       # record dropped
    assert out.exists()                          # output file NOT touched
    assert (src / "Dune.mp3").exists()           # source originals NOT touched (that's delete-sources)
