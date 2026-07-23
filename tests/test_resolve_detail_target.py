"""After a re-run re-resolves a folder, the detail pane must follow the book even when a
multi-book re-group churns its id. resolve_detail_target picks the right book to show."""

from pathlib import Path

from colophon.adapters.config import Config
from colophon.app_context import AppContext
from colophon.controller import AppController
from colophon.core.graph import leaf_id_for
from colophon.core.models import BookUnit, SourceFile


def _ctx(tmp_path):
    return AppContext.create(Config(
        db_path=tmp_path / "db.sqlite", library_root=tmp_path / "lib",
        scan_paths=[tmp_path / "ingest"]))


def _book(folder: Path, *names: str) -> BookUnit:
    paths = [folder / n for n in names]
    b = BookUnit.new(source_folder=folder)
    b.id = leaf_id_for(folder, paths)
    b.source_files = [SourceFile(path=p, size=1, duration_seconds=60.0, ext=".mp3") for p in paths]
    return b


def test_returns_original_id_when_it_survives(tmp_path):
    ctx = _ctx(tmp_path)
    c = AppController(ctx)
    folder = tmp_path / "Folder"
    a = _book(folder, "01.mp3")
    ctx.books.upsert(a)

    assert c.resolve_detail_target(a) == a.id
    ctx.close()


def test_falls_back_to_overlapping_sibling_when_id_churned(tmp_path):
    ctx = _ctx(tmp_path)
    c = AppController(ctx)
    folder = tmp_path / "Folder"
    original = _book(folder, "01.mp3", "02.mp3")  # never upserted -> its id is gone (re-grouped)
    survivor = _book(folder, "02.mp3", "03.mp3")  # the re-group's book inherited 02.mp3
    other = _book(folder, "99.mp3")
    ctx.books.upsert(survivor)
    ctx.books.upsert(other)

    assert c.resolve_detail_target(original) == survivor.id  # most file overlap wins
    ctx.close()


def test_returns_none_when_book_and_its_files_are_gone(tmp_path):
    ctx = _ctx(tmp_path)
    c = AppController(ctx)
    folder = tmp_path / "Folder"
    original = _book(folder, "01.mp3")  # never upserted, and nothing inherited its file
    ctx.books.upsert(_book(folder, "99.mp3"))

    assert c.resolve_detail_target(original) is None
    ctx.close()
