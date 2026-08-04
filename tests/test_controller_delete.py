from pathlib import Path

from mutagen.id3 import ID3, TALB, TIT2, TPE1

from colophon.adapters.config import Config
from colophon.app_context import AppContext
from colophon.controller import AppController


def _mp3(path: Path, artist: str = "Some Author") -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"")
    tags = ID3()
    tags.add(TPE1(encoding=3, text=[artist]))
    tags.save(path)


def _mp3_tagged(path: Path, *, album: str, title: str, artist: str = "Some Author") -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"")
    tags = ID3()
    tags.add(TPE1(encoding=3, text=[artist]))
    tags.add(TALB(encoding=3, text=[album]))
    tags.add(TIT2(encoding=3, text=[title]))
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
    ctx, ctrl, _ingest = _ctrl(tmp_path)
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


def test_empty_folders_under_scan_paths_finds_empties(tmp_path):
    ctx, ctrl, ingest = _ctrl(tmp_path)
    (ingest / "HasFile").mkdir(parents=True)
    (ingest / "HasFile" / "keep.txt").write_bytes(b"x")
    (ingest / "Empty").mkdir()
    (ingest / "Nested" / "inner").mkdir(parents=True)  # holds only an empty subdir

    found = set(ctrl.empty_folders_under_scan_paths())

    assert (ingest / "Empty") in found
    assert (ingest / "Nested" / "inner") in found
    assert (ingest / "Nested") in found          # parent of only-empty-subdir counts
    assert (ingest / "HasFile") not in found      # has a file
    assert ingest not in found                    # never the scan root itself
    ctx.close()


def test_empty_folders_empty_when_no_scan_paths(tmp_path):
    ctx = AppContext.create(Config(db_path=tmp_path / "db.sqlite", library_root=tmp_path / "lib"))
    ctrl = AppController(ctx)
    assert ctrl.empty_folders_under_scan_paths() == []
    ctx.close()


def test_delete_book_from_disk_multi_book_folder_keeps_sibling(tmp_path):
    # Two books share one folder (a dedup, loose files). Deleting one removes only its files and
    # record; the sibling's files + record stay, and the folder is kept.
    ctx, ctrl, ingest = _ctrl(tmp_path)
    dup = ingest / "Dup"
    _mp3_tagged(dup / "Alpha 01.mp3", album="Alpha", title="Alpha 01")
    _mp3_tagged(dup / "Alpha 02.mp3", album="Alpha", title="Alpha 02")
    _mp3_tagged(dup / "Beta 01.mp3", album="Beta", title="Beta 01")
    _mp3_tagged(dup / "Beta 02.mp3", album="Beta", title="Beta 02")
    ctrl.scan([ingest])
    books = [ctx.books.get(i) for i in ctx.books.ids_in_folder(dup)]
    assert len(books) == 2  # the scan split the shared folder into two books
    alpha = next(b for b in books if any("Alpha" in sf.path.name for sf in b.source_files))
    beta = next(b for b in books if b.id != alpha.id)

    result = ctrl.delete_book_from_disk(alpha)

    assert result.book_removed is True and result.folder_removed is False
    assert result.files_deleted == 2 and result.errors == ()
    assert not (dup / "Alpha 01.mp3").exists()
    assert (dup / "Beta 01.mp3").exists() and (dup / "Beta 02.mp3").exists()
    assert dup.exists()
    remaining = list(ctx.books.ids_in_folder(dup))
    assert remaining == [beta.id]
    ctx.close()


def test_delete_book_from_disk_last_book_removes_folder(tmp_path):
    ctx, ctrl, ingest = _ctrl(tmp_path)
    solo = ingest / "Solo"
    _mp3(solo / "01.mp3")
    _mp3(solo / "02.mp3")
    ctrl.scan([ingest])
    book = _book_in(ctx, solo)

    result = ctrl.delete_book_from_disk(book)

    assert result.book_removed is True and result.folder_removed is True
    assert not solo.exists()
    assert _book_in(ctx, solo) is None
    ctx.close()


def test_delete_book_from_disk_removes_nonaudio_only_on_full_folder_removal(tmp_path):
    # A cover in the folder survives while a sibling remains, and is swept when the last book goes.
    ctx, ctrl, ingest = _ctrl(tmp_path)
    dup = ingest / "Dup"
    _mp3_tagged(dup / "Alpha 01.mp3", album="Alpha", title="Alpha 01")
    _mp3_tagged(dup / "Beta 01.mp3", album="Beta", title="Beta 01")
    (dup / "cover.jpg").write_bytes(b"")
    ctrl.scan([ingest])
    books = [ctx.books.get(i) for i in ctx.books.ids_in_folder(dup)]
    alpha = next(b for b in books if any("Alpha" in sf.path.name for sf in b.source_files))
    beta = next(b for b in books if b.id != alpha.id)

    ctrl.delete_book_from_disk(alpha)
    assert (dup / "cover.jpg").exists()          # kept: a sibling still lives here

    result = ctrl.delete_book_from_disk(beta)
    assert result.folder_removed is True
    assert not dup.exists()                       # the cover went with the folder
    ctx.close()


def test_delete_book_from_disk_never_removes_scan_root(tmp_path):
    # A book whose source folder IS a scan root: files are deleted, the folder is never removed.
    ctx, ctrl, ingest = _ctrl(tmp_path)
    _mp3(ingest / "01.mp3")  # a loose book directly in the scan root
    ctrl.scan([ingest])
    book = _book_in(ctx, ingest)
    assert book is not None

    result = ctrl.delete_book_from_disk(book)

    assert result.book_removed is True and result.folder_removed is False
    assert ingest.exists()                        # the scan root is never rmtree'd
    ctx.close()


def test_delete_book_from_disk_partial_failure_keeps_book_and_folder(tmp_path, monkeypatch):
    ctx, ctrl, ingest = _ctrl(tmp_path)
    solo = ingest / "Solo"
    _mp3(solo / "01.mp3")
    _mp3(solo / "02.mp3")
    ctrl.scan([ingest])
    book = _book_in(ctx, solo)
    keep = book.source_files[0].path
    # Simulate one file that cannot be unlinked: delete everything except `keep`.
    monkeypatch.setattr(
        "colophon.controller.file_ops.delete_files_from_disk",
        lambda paths: [p for p in paths if p != keep],
    )

    result = ctrl.delete_book_from_disk(book)

    assert result.book_removed is False and result.folder_removed is False
    assert result.errors and _book_in(ctx, solo) is not None   # book kept
    assert [sf.path.name for sf in _book_in(ctx, solo).source_files] == ["01.mp3"]
    assert solo.exists()
    ctx.close()


def test_folder_kept_after_book_delete_preview(tmp_path):
    ctx, ctrl, ingest = _ctrl(tmp_path)
    dup = ingest / "Dup"
    _mp3_tagged(dup / "Alpha 01.mp3", album="Alpha", title="Alpha 01")
    _mp3_tagged(dup / "Beta 01.mp3", album="Beta", title="Beta 01")
    ctrl.scan([ingest])
    books = [ctx.books.get(i) for i in ctx.books.ids_in_folder(dup)]
    alpha = next(b for b in books if any("Alpha" in sf.path.name for sf in b.source_files))
    solo = ingest / "Solo"
    _mp3(solo / "01.mp3")
    ctrl.scan([ingest])
    solo_book = _book_in(ctx, solo)

    assert ctrl.folder_kept_after_book_delete(alpha) is True    # Beta remains in Dup
    assert ctrl.folder_kept_after_book_delete(solo_book) is False  # nothing else in Solo
    ctx.close()
