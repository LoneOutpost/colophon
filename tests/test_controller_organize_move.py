import asyncio
from pathlib import Path

from mutagen.id3 import ID3, TPE1

from colophon.adapters.config import Config
from colophon.app_context import AppContext
from colophon.controller import AppController, EncodeJobOptions


def _mp3(path: Path, artist: str = "Some Author") -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"")
    tags = ID3()
    tags.add(TPE1(encoding=3, text=[artist]))
    tags.save(path)


def _ctrl(tmp_path, library_root: Path):
    ingest = tmp_path / "ingest"
    ctx = AppContext.create(
        Config(db_path=tmp_path / "db.sqlite", library_root=library_root, scan_paths=[ingest])
    )
    return ctx, AppController(ctx), ingest


def _book_in(ctx, folder: Path):
    ids = list(ctx.books.ids_in_folder(folder))
    return ctx.books.get(ids[0]) if ids else None


def _mp3s_under(root: Path) -> list[Path]:
    return sorted(root.rglob("*.mp3"))


def test_organize_move_dest_inside_scan_path_retracks_book(tmp_path):
    ingest = tmp_path / "ingest"
    library = ingest / "library"
    ctx, ctrl, ingest = _ctrl(tmp_path, library)
    _mp3(ingest / "Dune" / "01.mp3")
    ctrl.scan([ingest])
    book = _book_in(ctx, ingest / "Dune")

    asyncio.run(ctrl.run_encode_job(
        [book], EncodeJobOptions(encode=False, organize=True, delete_sources=True)))

    assert _book_in(ctx, ingest / "Dune") is None
    assert not (ingest / "Dune" / "01.mp3").exists()
    organized = _mp3s_under(library)
    assert len(organized) == 1
    all_books = ctx.books.list_all()
    assert len(all_books) == 1
    assert library in all_books[0].source_folder.parents or all_books[0].source_folder == organized[0].parent
    ctx.close()


def test_organize_move_dest_outside_scan_path_removes_from_library(tmp_path):
    library = tmp_path / "library_outside"
    ctx, ctrl, ingest = _ctrl(tmp_path, library)
    _mp3(ingest / "Dune" / "01.mp3")
    ctrl.scan([ingest])
    book = _book_in(ctx, ingest / "Dune")

    asyncio.run(ctrl.run_encode_job(
        [book], EncodeJobOptions(encode=False, organize=True, delete_sources=True)))

    assert _book_in(ctx, ingest / "Dune") is None
    assert len(ctx.books.list_all()) == 0
    assert len(_mp3s_under(library)) == 1
    ctx.close()


def test_organize_copy_leaves_source_book_and_copies_file(tmp_path):
    library = tmp_path / "library_outside"
    ctx, ctrl, ingest = _ctrl(tmp_path, library)
    _mp3(ingest / "Dune" / "01.mp3")
    ctrl.scan([ingest])
    book = _book_in(ctx, ingest / "Dune")

    asyncio.run(ctrl.run_encode_job(
        [book], EncodeJobOptions(encode=False, organize=True, delete_sources=False)))

    assert _book_in(ctx, ingest / "Dune") is not None
    assert (ingest / "Dune" / "01.mp3").exists()
    assert len(_mp3s_under(library)) == 1
    ctx.close()


def test_move_without_organize_or_encode_does_not_remove_book(tmp_path):
    # delete_sources with neither organize nor encode is a no-op combo: nothing was placed,
    # so the book must NOT be removed from the library and its files must stay put.
    library = tmp_path / "library_outside"
    ctx, ctrl, ingest = _ctrl(tmp_path, library)
    _mp3(ingest / "Dune" / "01.mp3")
    ctrl.scan([ingest])
    book = _book_in(ctx, ingest / "Dune")

    asyncio.run(ctrl.run_encode_job(
        [book], EncodeJobOptions(encode=False, organize=False, delete_sources=True)))

    assert _book_in(ctx, ingest / "Dune") is not None
    assert (ingest / "Dune" / "01.mp3").exists()
    ctx.close()


def test_reorg_config_flag_does_not_force_delete_over_explicit_copy(tmp_path):
    # The reorg_delete_sources config flag seeds the dialog's default toggle, but must NOT
    # override an explicit Copy (delete_sources=False): originals are kept and the book stays.
    ingest = tmp_path / "ingest"
    library = tmp_path / "library_outside"
    ctx = AppContext.create(Config(
        db_path=tmp_path / "db.sqlite", library_root=library,
        scan_paths=[ingest], reorg_delete_sources=True))
    ctrl = AppController(ctx)
    _mp3(ingest / "Dune" / "01.mp3")
    ctrl.scan([ingest])
    book = _book_in(ctx, ingest / "Dune")

    asyncio.run(ctrl.run_encode_job(
        [book], EncodeJobOptions(encode=False, organize=True, delete_sources=False)))

    assert (ingest / "Dune" / "01.mp3").exists()        # Copy kept the original
    assert _book_in(ctx, ingest / "Dune") is not None    # book stays in the library
    ctx.close()


def test_organize_move_failed_book_keeps_sources(tmp_path):
    # "1.mp3" and "01.mp3" share a natural-sort key, so the part order is ambiguous and
    # organize fails ("couldn't order parts"). A failed organize must NOT delete the sources
    # and must leave the book in the library.
    library = tmp_path / "library_outside"
    ctx, ctrl, ingest = _ctrl(tmp_path, library)
    _mp3(ingest / "Multi" / "1.mp3")
    _mp3(ingest / "Multi" / "01.mp3")
    ctrl.scan([ingest])
    book = _book_in(ctx, ingest / "Multi")

    result = asyncio.run(ctrl.run_encode_job(
        [book], EncodeJobOptions(encode=False, organize=True, delete_sources=True)))

    assert result.results[0].status == "failed"
    assert (ingest / "Multi" / "1.mp3").exists()
    assert (ingest / "Multi" / "01.mp3").exists()
    assert _book_in(ctx, ingest / "Multi") is not None
    ctx.close()


def test_organize_move_auto_removes_empty_source_folder(tmp_path):
    ingest = tmp_path / "ingest"
    library = tmp_path / "library_outside"
    ctx, ctrl, ingest = _ctrl(tmp_path, library)
    _mp3(ingest / "Solo" / "01.mp3")
    ctrl.scan([ingest])
    book = _book_in(ctx, ingest / "Solo")

    asyncio.run(ctrl.run_encode_job(
        [book], EncodeJobOptions(encode=False, organize=True, delete_sources=True)))

    assert not (ingest / "Solo").exists()   # emptied source folder auto-removed
    ctx.close()


def test_organize_move_keeps_source_folder_with_sidecar(tmp_path):
    ingest = tmp_path / "ingest"
    library = tmp_path / "library_outside"
    ctx, ctrl, ingest = _ctrl(tmp_path, library)
    _mp3(ingest / "Solo" / "01.mp3")
    (ingest / "Solo" / "cover.jpg").write_bytes(b"art")  # non-audio leftover
    ctrl.scan([ingest])
    book = _book_in(ctx, ingest / "Solo")

    asyncio.run(ctrl.run_encode_job(
        [book], EncodeJobOptions(encode=False, organize=True, delete_sources=True)))

    assert (ingest / "Solo").exists()               # folder kept because a sidecar remains
    assert (ingest / "Solo" / "cover.jpg").exists()
    ctx.close()


def test_organize_move_rederive_is_folder_scoped(tmp_path):
    # The post-move re-derive must re-scan only the destination FOLDER, not its whole scan root.
    # Reproduction: an unrelated folder with audio appears under the scan root; an organize-move
    # must NOT sweep it in (a full-root rescan would ingest it).
    ingest = tmp_path / "ingest"
    library = ingest / "library"  # destination is inside the scan root -> re-track path runs
    ctx, ctrl, ingest = _ctrl(tmp_path, library)
    _mp3(ingest / "Solo" / "01.mp3")
    ctrl.scan([ingest])
    book = _book_in(ctx, ingest / "Solo")
    _mp3(ingest / "Unrelated" / "01.mp3")  # new folder on disk, not yet scanned
    assert _book_in(ctx, ingest / "Unrelated") is None

    asyncio.run(ctrl.run_encode_job(
        [book], EncodeJobOptions(encode=False, organize=True, delete_sources=True)))

    # The organized book is re-tracked at its destination, but the unrelated folder must not be
    # discovered by the scoped re-derive.
    assert _book_in(ctx, ingest / "Unrelated") is None
    ctx.close()


def _persist_opts(**kw):
    from colophon.adapters.lazylibrarian import PathPatterns
    return EncodeJobOptions(encode=False, organize=True, delete_sources=False,
                            patterns=PathPatterns(folder="$Author/$Title", single_file="$Title"), **kw)


def test_persist_clash_is_skipped_not_failed(tmp_path):
    from colophon.core.models import Phase, PhaseState
    from colophon.core.phases import state_of
    library = tmp_path / "ingest"                       # library == scan path so layout lines up
    ctx, ctrl, ingest = _ctrl(tmp_path, library)
    _mp3(ingest / "Some Author" / "Dune" / "01.mp3")    # source name != target name
    ctrl.scan([ingest])
    book = _book_in(ctx, ingest / "Some Author" / "Dune")
    # occupy the target with a DIFFERENT file, after the scan so it isn't part of the book
    (ingest / "Some Author" / "Dune" / "Dune.mp3").write_bytes(b"a different book")

    res = ctrl._persist_book(book, _persist_opts())
    assert res.status == "skipped"
    assert "occupied" in (res.detail or "").lower()
    assert (ingest / "Some Author" / "Dune" / "Dune.mp3").read_bytes() == b"a different book"
    assert state_of(ctx.books.get(book.id), Phase.ORGANIZE) is not PhaseState.FAILED
    ctx.close()


def test_persist_already_placed_is_done_and_tags(tmp_path):
    from colophon.core.models import Phase, PhaseState
    from colophon.core.phases import state_of
    library = tmp_path / "ingest"
    ctx, ctrl, ingest = _ctrl(tmp_path, library)
    _mp3(ingest / "Some Author" / "Dune" / "Dune.mp3")  # already in $Author/$Title/$Title layout
    ctrl.scan([ingest])
    book = _book_in(ctx, ingest / "Some Author" / "Dune")

    res = ctrl._persist_book(book, _persist_opts())
    assert res.status == "done", (res.status, res.detail)
    assert (ingest / "Some Author" / "Dune" / "Dune.mp3").exists()   # not moved/removed
    assert state_of(ctx.books.get(book.id), Phase.ORGANIZE) is PhaseState.FRESH
    ctx.close()
