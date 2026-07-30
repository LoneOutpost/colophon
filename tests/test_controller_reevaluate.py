from pathlib import Path

from mutagen.id3 import ID3, TPE1

from colophon.adapters.config import Config
from colophon.app_context import AppContext
from colophon.controller import AppController
from colophon.services.resolve import ResolveResult


def _mp3(path: Path, artist: str = "Some Author") -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"")
    tags = ID3()
    tags.add(TPE1(encoding=3, text=[artist]))
    tags.save(path)


def _ctx(tmp_path):
    ingest = tmp_path / "ingest"
    ctx = AppContext.create(
        Config(db_path=tmp_path / "db.sqlite", library_root=tmp_path / "lib", scan_paths=[ingest])
    )
    return ctx, AppController(ctx), ingest


def _book_in(ctx, folder):
    ids = list(ctx.books.ids_in_folder(folder))
    return ctx.books.get(ids[0]) if ids else None


def test_reevaluate_scope_persists_new_file_and_reports(tmp_path):
    ctx, ctrl, ingest = _ctx(tmp_path)
    _mp3(ingest / "Dune" / "01.mp3")
    ctrl.scan([ingest])
    book = _book_in(ctx, ingest / "Dune")
    _mp3(ingest / "Dune" / "02.mp3")

    result = ctrl.reevaluate_scope(book)

    assert isinstance(result, ResolveResult)
    assert [p.name for p in result.changes.added] == ["02.mp3"]
    persisted = _book_in(ctx, ingest / "Dune")
    assert [sf.path.name for sf in persisted.source_files] == ["01.mp3", "02.mp3"]
    assert persisted.id == book.id  # book scope keys on source_folder: id is stable
    ctx.close()


def test_reevaluate_scope_noop_reports_no_changes(tmp_path):
    ctx, ctrl, ingest = _ctx(tmp_path)
    _mp3(ingest / "Dune" / "01.mp3")
    ctrl.scan([ingest])
    book = _book_in(ctx, ingest / "Dune")

    result = ctrl.reevaluate_scope(book)

    assert result.changes.is_empty
    assert result.changes.summary() == "No changes on disk"
    ctx.close()


def test_reevaluate_folder_reclusters_and_reports(tmp_path):
    ctx, ctrl, ingest = _ctx(tmp_path)
    _mp3(ingest / "Dune" / "01.mp3")
    ctrl.scan([ingest])
    _mp3(ingest / "Dune" / "02.mp3")  # a new part appeared on disk

    result = ctrl.reevaluate_folder(ingest / "Dune")

    assert [p.name for p in result.changes.added] == ["02.mp3"]
    book = _book_in(ctx, ingest / "Dune")
    assert [sf.path.name for sf in book.source_files] == ["01.mp3", "02.mp3"]  # re-clustered together
    ctx.close()


def test_reevaluate_folder_is_scoped_not_a_full_scan(tmp_path):
    ctx, ctrl, ingest = _ctx(tmp_path)
    _mp3(ingest / "Dune" / "01.mp3")
    ctrl.scan([ingest])
    _mp3(ingest / "Unrelated" / "01.mp3")  # new folder elsewhere under the scan root
    assert _book_in(ctx, ingest / "Unrelated") is None

    ctrl.reevaluate_folder(ingest / "Dune")

    assert _book_in(ctx, ingest / "Unrelated") is None  # folder scope must not walk the whole root
    ctx.close()


def test_reevaluate_folder_empty_folder_is_a_noop_not_a_full_scan(tmp_path):
    # A book-less folder (e.g. a container directory) must do nothing — not fall through to a
    # full scan of every scan path (the exact bug this feature prevents).
    ctx, ctrl, ingest = _ctx(tmp_path)
    _mp3(ingest / "Dune" / "01.mp3")
    ctrl.scan([ingest])
    _mp3(ingest / "Unrelated" / "01.mp3")  # new folder on disk, not yet scanned
    (ingest / "Container").mkdir()          # a folder with no books
    assert _book_in(ctx, ingest / "Unrelated") is None

    result = ctrl.reevaluate_folder(ingest / "Container")

    assert result.plan.units == []
    assert result.changes.is_empty
    assert _book_in(ctx, ingest / "Unrelated") is None  # must NOT have full-scanned the root
    ctx.close()
