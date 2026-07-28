from pathlib import Path

from mutagen.id3 import ID3, TPE1

from colophon.adapters.config import Config
from colophon.app_context import AppContext
from colophon.controller import AppController


def _mp3(path: Path, artist: str = "Some Author") -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"")
    tags = ID3()
    tags.add(TPE1(encoding=3, text=[artist]))
    tags.save(path)


def _ctrl(tmp_path):
    ingest = tmp_path / "ingest"
    ctx = AppContext.create(
        Config(db_path=tmp_path / "db.sqlite", library_root=tmp_path / "lib", scan_paths=[ingest])
    )
    return ctx, AppController(ctx), ingest


def _book_in(ctx, folder: Path):
    ids = list(ctx.books.ids_in_folder(folder))
    return ctx.books.get(ids[0]) if ids else None


def test_delete_file_removes_one_and_keeps_book(tmp_path):
    ctx, ctrl, ingest = _ctrl(tmp_path)
    _mp3(ingest / "Dune" / "01.mp3")
    _mp3(ingest / "Dune" / "02.mp3")
    ctrl.scan([ingest])
    book = _book_in(ctx, ingest / "Dune")
    target = book.source_files[1].path

    result = ctrl.delete_file(book, target)

    assert result.files_deleted == 1 and result.book_removed is False
    assert not target.exists()
    kept = _book_in(ctx, ingest / "Dune")
    assert [sf.path.name for sf in kept.source_files] == ["01.mp3"]
    ctx.close()


def test_delete_last_file_removes_book(tmp_path):
    ctx, ctrl, ingest = _ctrl(tmp_path)
    _mp3(ingest / "Solo" / "01.mp3")
    ctrl.scan([ingest])
    book = _book_in(ctx, ingest / "Solo")

    result = ctrl.delete_file(book, book.source_files[0].path)

    assert result.files_deleted == 1 and result.book_removed is True
    assert _book_in(ctx, ingest / "Solo") is None
    ctx.close()
