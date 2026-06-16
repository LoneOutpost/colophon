from pathlib import Path

from colophon.adapters.repository.store import HistoryRepo, connect, migrate
from colophon.core.models import EditChange


def _repo(tmp_path: Path) -> HistoryRepo:
    conn = connect(tmp_path / "db.sqlite")
    migrate(conn)
    return HistoryRepo(conn)


def test_migrate_creates_edit_history_table(tmp_path: Path):
    conn = connect(tmp_path / "db.sqlite")
    migrate(conn)
    names = {r["name"] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    assert "edit_history" in names
    assert conn.execute("SELECT version FROM schema_version").fetchone()["version"] == 2


def test_record_and_list_batch_round_trips(tmp_path: Path):
    repo = _repo(tmp_path)
    changes = [
        EditChange(book_id="b1", field="title", old_value="Old", new_value="New"),
        EditChange(book_id="b1", field="asin", old_value=None, new_value="B0"),
    ]
    repo.record("batch-1", changes)
    got = repo.list_batch("batch-1")
    assert [(c.field, c.old_value, c.new_value) for c in got] == [
        ("title", "Old", "New"),
        ("asin", None, "B0"),
    ]


def test_latest_batch_id_returns_most_recent(tmp_path: Path):
    repo = _repo(tmp_path)
    repo.record("batch-1", [EditChange(book_id="b1", field="title", old_value="a", new_value="b")])
    repo.record("batch-2", [EditChange(book_id="b1", field="title", old_value="b", new_value="c")])
    assert repo.latest_batch_id() == "batch-2"


def test_mark_reverted_excludes_from_latest(tmp_path: Path):
    repo = _repo(tmp_path)
    repo.record("batch-1", [EditChange(book_id="b1", field="title", old_value="a", new_value="b")])
    repo.mark_reverted("batch-1")
    assert repo.latest_batch_id() is None
