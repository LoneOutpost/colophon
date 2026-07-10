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
