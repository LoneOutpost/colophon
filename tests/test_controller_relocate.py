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


def test_relocate_rename_in_place(tmp_path):
    ctx, ctrl, ingest = _ctrl(tmp_path)
    _mp3(ingest / "Dune" / "01.mp3")
    ctrl.scan([ingest])
    book = _book_in(ctx, ingest / "Dune")
    old = book.source_files[0].path

    result = ctrl.relocate_file(book, old, ingest / "Dune", "renamed.mp3")

    assert result.status == "renamed"
    assert not old.exists()
    assert (ingest / "Dune" / "renamed.mp3").exists()
    nav = ctx.books.get(result.book_id)
    assert [sf.path.name for sf in nav.source_files] == ["renamed.mp3"]
    ctx.close()


def test_relocate_move_into_new_folder_under_scan_root(tmp_path):
    ctx, ctrl, ingest = _ctrl(tmp_path)
    _mp3(ingest / "Src" / "01.mp3")
    _mp3(ingest / "Src" / "02.mp3")
    ctrl.scan([ingest])
    book = _book_in(ctx, ingest / "Src")
    moved = ingest / "Src" / "02.mp3"

    result = ctrl.relocate_file(book, moved, ingest / "Dest")

    assert result.status == "regrouped"
    assert (ingest / "Dest" / "02.mp3").exists()
    assert not moved.exists()
    src_book = _book_in(ctx, ingest / "Src")
    assert [sf.path.name for sf in src_book.source_files] == ["01.mp3"]
    dest_book = _book_in(ctx, ingest / "Dest")
    assert dest_book is not None
    assert [sf.path.name for sf in dest_book.source_files] == ["02.mp3"]
    ctx.close()


def test_relocate_move_outside_scan_root_leaves_library(tmp_path):
    ctx, ctrl, ingest = _ctrl(tmp_path)
    _mp3(ingest / "Src" / "01.mp3")
    _mp3(ingest / "Src" / "02.mp3")
    ctrl.scan([ingest])
    book = _book_in(ctx, ingest / "Src")
    moved = ingest / "Src" / "02.mp3"
    outside = tmp_path / "elsewhere"

    result = ctrl.relocate_file(book, moved, outside)

    assert result.status == "left_library"
    assert (outside / "02.mp3").exists()
    src_book = _book_in(ctx, ingest / "Src")
    assert [sf.path.name for sf in src_book.source_files] == ["01.mp3"]
    assert not any(
        sf.path.name == "02.mp3"
        for b in ctx.books.list_all() for sf in b.source_files
    )
    ctx.close()


def test_relocate_collision_returns_error_without_moving(tmp_path):
    ctx, ctrl, ingest = _ctrl(tmp_path)
    _mp3(ingest / "Src" / "01.mp3")
    ctrl.scan([ingest])
    book = _book_in(ctx, ingest / "Src")
    moved = book.source_files[0].path
    _mp3(ingest / "Dest" / "01.mp3")

    result = ctrl.relocate_file(book, moved, ingest / "Dest")

    assert result.error is not None
    assert result.status == "error"
    assert moved.exists()
    ctx.close()


def test_path_within_scan_paths(tmp_path):
    ctx, ctrl, ingest = _ctrl(tmp_path)
    assert ctrl.path_within_scan_paths(ingest / "Anything") is True
    assert ctrl.path_within_scan_paths(ingest) is True
    assert ctrl.path_within_scan_paths(tmp_path / "outside") is False
    ctx.close()


def test_relocate_rename_in_multi_book_folder_navigates_to_correct_book(tmp_path):
    ctx, ctrl, ingest = _ctrl(tmp_path)
    folder = ingest / "Shared"
    _mp3(folder / "bookA.mp3")
    _mp3(folder / "bookB.mp3")
    ctrl.scan([ingest])
    # Force the folder into two single-file books via a partition, then re-derive.
    ctx.grouping.set_partition(str(folder), [["bookA.mp3"], ["bookB.mp3"]])
    ctrl.apply_scan(ctrl.scan_preview([ingest]))
    books = [ctx.books.get(i) for i in ctx.books.ids_in_folder(folder)]
    book_a = next(b for b in books if b.source_files[0].path.name == "bookA.mp3")

    result = ctrl.relocate_file(book_a, book_a.source_files[0].path, folder, "bookA-renamed.mp3")

    assert result.status == "renamed"
    nav = ctx.books.get(result.book_id)
    assert nav is not None
    assert [sf.path.name for sf in nav.source_files] == ["bookA-renamed.mp3"]  # the book we operated on, not the sibling
    ctx.close()
