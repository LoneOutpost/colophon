from pathlib import Path

from colophon.adapters.repository.store import BookUnitRepo, HistoryRepo, connect, migrate
from colophon.core.models import BookUnit
from colophon.services.editing import bulk_set_field, remap_field, set_field_value
from colophon.services.undo import undo_batch, undo_last


def _repos(tmp_path):
    conn = connect(tmp_path / "db.sqlite")
    migrate(conn)
    return BookUnitRepo(conn), HistoryRepo(conn)


def test_undo_restores_single_edit(tmp_path):
    books, hist = _repos(tmp_path)
    b = BookUnit.new(source_folder=Path("/ingest/x"))
    b.title = "Original"
    books.upsert(b)
    batch = set_field_value(books, hist, b, "title", "Changed")
    undo_batch(books, hist, batch)
    assert books.get(b.id).title == "Original"
    assert hist.latest_batch_id() is None  # batch marked reverted


def test_undo_restores_remap_including_cleared_source(tmp_path):
    books, hist = _repos(tmp_path)
    b = BookUnit.new(source_folder=Path("/ingest/x"))
    b.title = "Scott Brick"
    books.upsert(b)
    batch = remap_field(books, hist, b, src="title", dst="narrator", clear_source=True)
    undo_batch(books, hist, batch)
    restored = books.get(b.id)
    assert restored.title == "Scott Brick"
    assert restored.narrators == []


def test_undo_last_reverts_most_recent_batch(tmp_path):
    books, hist = _repos(tmp_path)
    a = BookUnit.new(source_folder=Path("/ingest/a"))
    a.publisher = "Old"
    books.upsert(a)
    bulk_set_field(books, hist, [a], "publisher", "New")
    assert undo_last(books, hist) is True
    assert books.get(a.id).publisher == "Old"


def test_undo_last_with_nothing_to_undo_returns_false(tmp_path):
    books, hist = _repos(tmp_path)
    assert undo_last(books, hist) is False


def test_undo_bulk_batch_restores_all_books(tmp_path):
    books, hist = _repos(tmp_path)
    a = BookUnit.new(source_folder=Path("/ingest/a"))
    a.publisher = "Pub A"
    b = BookUnit.new(source_folder=Path("/ingest/b"))
    b.publisher = "Pub B"
    books.upsert(a)
    books.upsert(b)
    batch = bulk_set_field(books, hist, [a, b], "publisher", "Unified")
    assert books.get(a.id).publisher == "Unified"
    assert books.get(b.id).publisher == "Unified"
    undo_batch(books, hist, batch)
    assert books.get(a.id).publisher == "Pub A"
    assert books.get(b.id).publisher == "Pub B"
