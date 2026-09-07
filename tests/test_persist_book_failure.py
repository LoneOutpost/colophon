"""A failure on any file is a failure of the whole book.

This matters most when organizing: the parts are moved first and tagged second, so a per-file tag
failure used to be a `logger.warning` while the book was still reported `done`. A real persist of
"Stand on Zanzibar" reported success with two of its files silently keeping their old tags, and the
only trace was terminal scrollback.
"""

from __future__ import annotations

import asyncio
from pathlib import Path

from mutagen.id3 import ID3, TPE1

from colophon.adapters.config import Config
from colophon.app_context import AppContext
from colophon.controller import AppController, EncodeJobOptions
from colophon.core.models import Phase, PhaseState
from colophon.core.phases import state_of


def _mp3(path: Path, artist: str = "Some Author") -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"")
    tags = ID3()
    tags.add(TPE1(encoding=3, text=[artist]))
    tags.save(path)


def _ctrl(tmp_path: Path, library: Path):
    ingest = tmp_path / "ingest"
    ctx = AppContext.create(
        Config(db_path=tmp_path / "db.sqlite", library_root=library, scan_paths=[ingest])
    )
    return ctx, AppController(ctx), ingest


def _book_in(ctx, folder: Path):
    ids = list(ctx.books.ids_in_folder(folder))
    return ctx.books.get(ids[0]) if ids else None


def _fail_tagging(monkeypatch, *, on: str | None = None) -> list[Path]:
    """Make tag_file fail — for every file, or only those whose name contains `on`."""
    attempted: list[Path] = []

    def _tag_file(path, _book, **_kwargs):
        attempted.append(path)
        return not (on is None or on in path.name)

    monkeypatch.setattr("colophon.controller.tag_file", _tag_file)
    return attempted


def _organize(ctrl, book, *, delete_sources: bool = False):
    return asyncio.run(ctrl.run_encode_job(
        [book],
        EncodeJobOptions(encode=False, organize=True, delete_sources=delete_sources),
    ))


def test_one_untaggable_part_fails_the_whole_book(tmp_path, monkeypatch):
    library = tmp_path / "library"
    ctx, ctrl, ingest = _ctrl(tmp_path, library)
    for n in (1, 2, 3):
        _mp3(ingest / "Zanzibar" / f"{n:02d} of 03.mp3")
    ctrl.scan([ingest])
    book = _book_in(ctx, ingest / "Zanzibar")
    _fail_tagging(monkeypatch, on="02")

    (result,) = _organize(ctrl, book).results

    assert result.status == "failed"
    assert "tagging failed" in (result.detail or "")
    assert "1 of 3" in (result.detail or "")
    ctx.close()


def test_the_other_parts_are_still_tagged(tmp_path, monkeypatch):
    # Best effort per file, one verdict per book: don't abandon the parts that would have worked.
    library = tmp_path / "library"
    ctx, ctrl, ingest = _ctrl(tmp_path, library)
    for n in (1, 2, 3):
        _mp3(ingest / "Zanzibar" / f"{n:02d} of 03.mp3")
    ctrl.scan([ingest])
    book = _book_in(ctx, ingest / "Zanzibar")
    attempted = _fail_tagging(monkeypatch, on="02")

    _organize(ctrl, book)

    assert len(attempted) == 3, "stopped at the first failure instead of tagging the rest"
    ctx.close()


def test_a_failed_book_still_records_where_its_files_landed(tmp_path, monkeypatch):
    # The move already happened. Dropping output_folder would strand the moved files: the
    # post-move re-derive and source-folder cleanup both key on it.
    library = tmp_path / "library"
    ctx, ctrl, ingest = _ctrl(tmp_path, library)
    _mp3(ingest / "Zanzibar" / "01.mp3")
    ctrl.scan([ingest])
    book = _book_in(ctx, ingest / "Zanzibar")
    _fail_tagging(monkeypatch)

    (result,) = _organize(ctrl, book).results

    assert result.status == "failed"
    assert result.output_folder is not None
    assert library in result.output_folder.parents or result.output_folder.parent == library
    ctx.close()


def test_a_moved_book_that_failed_tagging_is_not_left_behind(tmp_path, monkeypatch):
    # delete_sources: the originals are gone, so the record MUST follow the files to their new
    # home. Reporting failure cannot mean skipping that bookkeeping, or the book reads as missing.
    library = tmp_path / "library_outside"
    ctx, ctrl, ingest = _ctrl(tmp_path, library)
    _mp3(ingest / "Zanzibar" / "01.mp3")
    ctrl.scan([ingest])
    book = _book_in(ctx, ingest / "Zanzibar")
    _fail_tagging(monkeypatch)

    (result,) = _organize(ctrl, book, delete_sources=True).results

    assert result.status == "failed"
    assert not (ingest / "Zanzibar" / "01.mp3").exists()      # the file really moved
    assert _book_in(ctx, ingest / "Zanzibar") is None          # no record left pointing at nothing
    ctx.close()


def test_the_tag_phase_carries_the_reason(tmp_path, monkeypatch):
    # Organized but untagged is the honest state: ORGANIZE succeeded, TAG did not.
    library = tmp_path / "library"
    ctx, ctrl, ingest = _ctrl(tmp_path, library)
    _mp3(ingest / "Zanzibar" / "01.mp3")
    ctrl.scan([ingest])
    book = _book_in(ctx, ingest / "Zanzibar")
    _fail_tagging(monkeypatch)

    _organize(ctrl, book)

    stored = ctx.books.get(book.id)
    assert state_of(stored, Phase.TAG) is PhaseState.FAILED
    ctx.close()


def test_a_clean_organize_is_still_done(tmp_path):
    library = tmp_path / "library"
    ctx, ctrl, ingest = _ctrl(tmp_path, library)
    for n in (1, 2):
        _mp3(ingest / "Zanzibar" / f"{n:02d} of 02.mp3")
    ctrl.scan([ingest])
    book = _book_in(ctx, ingest / "Zanzibar")

    (result,) = _organize(ctrl, book).results

    assert result.status == "done"
    assert result.output_folder is not None
    ctx.close()
