from pathlib import Path

from colophon.adapters.config import Config
from colophon.app_context import AppContext
from colophon.controller import AppController


def _untagged(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"")  # no embedded tags -> identity must come from folder + filename


def _ctrl(tmp_path):
    ingest = tmp_path / "audio"
    ctx = AppContext.create(Config(db_path=tmp_path / "db.sqlite", library_root=tmp_path / "lib",
                                   scan_paths=[ingest]))
    return ctx, AppController(ctx), ingest


def test_track_of_total_folder_identifies_as_one_book_by_parent_author(tmp_path):
    ctx, ctrl, ingest = _ctrl(tmp_path)
    folder = ingest / "Aldous Huxley" / "Crome Yellow"
    for i in range(1, 13):
        _untagged(folder / f"{i:02d}-12 - Chrome Yellow - Aldous Huxley.mp3")
    ctrl.scan([ingest])

    ids = list(ctx.books.ids_in_folder(folder))
    books = [ctx.books.get(i) for i in ids]
    assert len(books) == 1, f"expected 1 book, got {len(books)}"
    b = books[0]
    assert b.title in ("Chrome Yellow", "Crome Yellow"), b.title
    assert b.authors == ["Aldous Huxley"], b.authors
    ctx.close()
