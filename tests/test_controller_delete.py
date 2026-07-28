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


def test_delete_folder_removes_tree_and_nested_books(tmp_path):
    ctx, ctrl, ingest = _ctrl(tmp_path)
    _mp3(ingest / "Series" / "BookA" / "01.mp3")
    _mp3(ingest / "Series" / "BookB" / "01.mp3")
    ctrl.scan([ingest])
    assert len(ctx.books.list_all()) == 2

    result = ctrl.delete_folder(ingest / "Series")

    assert result.ok is True
    assert result.books_removed == 2
    assert not (ingest / "Series").exists()
    assert len(ctx.books.list_all()) == 0
    ctx.close()


def test_delete_folder_refuses_scan_root(tmp_path):
    ctx, ctrl, ingest = _ctrl(tmp_path)
    _mp3(ingest / "Dune" / "01.mp3")
    ctrl.scan([ingest])

    result = ctrl.delete_folder(ingest)  # the scan root itself

    assert result.ok is False and result.error is not None
    assert ingest.exists()
    assert len(ctx.books.list_all()) == 1
    ctx.close()


def test_delete_folder_refuses_outside_scan_paths(tmp_path):
    ctx, ctrl, ingest = _ctrl(tmp_path)
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "keep.txt").write_bytes(b"x")

    result = ctrl.delete_folder(outside)

    assert result.ok is False and result.error is not None
    assert outside.exists()
    ctx.close()


def test_delete_folder_refuses_when_no_scan_paths(tmp_path):
    # The most safety-critical guard: a misconfigured (empty) scan_paths must delete nothing.
    ctx, ctrl, ingest = _ctrl(tmp_path)
    _mp3(ingest / "Dune" / "01.mp3")
    ctrl.scan([ingest])
    ctx.config.scan_paths = []  # simulate a wiped/half-written config

    result = ctrl.delete_folder(ingest / "Dune")

    assert result.ok is False and result.error is not None
    assert (ingest / "Dune").exists()
    assert len(ctx.books.list_all()) == 1
    ctx.close()


def test_delete_folder_empty_in_scope_folder_no_books(tmp_path):
    # An in-scope folder with no books (a leftover) is deleted; the affected==[] branch.
    ctx, ctrl, ingest = _ctrl(tmp_path)
    ingest.mkdir(parents=True, exist_ok=True)
    leftover = ingest / "Leftover"
    leftover.mkdir()

    result = ctrl.delete_folder(leftover)

    assert result.ok is True and result.books_removed == 0
    assert not leftover.exists()
    ctx.close()


def test_delete_folder_rmtree_failure_leaves_records_intact(tmp_path, monkeypatch):
    # The crux safety invariant: if the on-disk delete fails, no book record is dropped.
    ctx, ctrl, ingest = _ctrl(tmp_path)
    _mp3(ingest / "Dune" / "01.mp3")
    ctrl.scan([ingest])
    monkeypatch.setattr(
        "colophon.controller.file_ops.delete_directory_from_disk", lambda folder: False
    )

    result = ctrl.delete_folder(ingest / "Dune")

    assert result.ok is False and result.error is not None
    assert result.books_removed == 0
    assert _book_in(ctx, ingest / "Dune") is not None  # record kept
    ctx.close()
