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
    assert row.disposition == "move"
    assert row.blocked is False


def test_organize_preview_flags_clash(tmp_path):
    ctx = _ctx(tmp_path)
    ctrl = AppController(ctx)
    book = _book(ctx, tmp_path)
    target = dict(ctrl.organize_targets([book]))[book.id]
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(b"a different file")   # a foreign file at the m4b target
    (row,) = ctrl.organize_preview([book])
    assert row.disposition == "clash"


def test_organize_preview_flags_already_placed(tmp_path):
    ctx = _ctx(tmp_path)
    ctrl = AppController(ctx)
    book = _book(ctx, tmp_path)
    target = dict(ctrl.organize_targets([book]))[book.id]
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(b"m4b")
    book.output_path = target                 # the book's own file already at the target
    ctx.books.upsert(book)
    (row,) = ctrl.organize_preview([book])
    assert row.disposition == "placed"


def test_organize_preview_flags_blocked(tmp_path):
    ctx = _ctx(tmp_path)
    ctrl = AppController(ctx)
    book = _book(ctx, tmp_path)
    book.missing = True   # a missing book is a blocking error
    ctx.books.upsert(book)
    (row,) = ctrl.organize_preview([book])
    assert row.blocked is True
    assert not row.target.exists()


def test_organize_preview_reorg_is_file_level_not_directory(tmp_path):
    # Without encode, a reorg copies the originals into the book folder; the preview shows that folder
    # and classifies per FILE — an unrelated file in the folder is not a clash (only a target file taken
    # by a different file is).
    ctx = _ctx(tmp_path)
    ctrl = AppController(ctx)
    book = _book(ctx, tmp_path)
    target = dict(ctrl.organize_targets([book]))[book.id]

    (row,) = ctrl.organize_preview([book], encode=False)
    assert row.target == target.parent          # destination folder, not a fake .m4b path
    assert row.disposition == "move"            # folder doesn't exist yet

    target.parent.mkdir(parents=True, exist_ok=True)
    (target.parent / "unrelated.mp3").write_bytes(b"x")   # other content, not our target file
    (row2,) = ctrl.organize_preview([book], encode=False)
    assert row2.disposition == "move"           # per-file: an unrelated file is not a clash

    # occupy the book's own reorg target file with a different file -> clash
    (dst,) = [d for _c, d in ctrl._reorg_pairs(book, ctx.patterns, ctx.config.library_root)]
    dst.write_bytes(b"a different file")
    (row3,) = ctrl.organize_preview([book], encode=False)
    assert row3.disposition == "clash"


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
