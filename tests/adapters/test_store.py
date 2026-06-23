from pathlib import Path

from colophon.adapters.repository.store import BookUnitRepo, OperationRepo, connect, migrate
from colophon.core.models import BookState, BookUnit, OperationRecord


def _table_names(conn):
    rows = conn.execute(
        "SELECT name FROM sqlite_master WHERE type = 'table'"
    ).fetchall()
    return {r["name"] for r in rows}


def test_migrate_creates_tables_and_sets_version(tmp_path: Path):
    conn = connect(tmp_path / "colophon.db")
    migrate(conn)
    tables = _table_names(conn)
    assert "book_units" in tables
    assert "schema_version" in tables
    version = conn.execute("SELECT version FROM schema_version").fetchone()["version"]
    assert version == 3


def test_migrate_is_idempotent(tmp_path: Path):
    conn = connect(tmp_path / "colophon.db")
    migrate(conn)
    migrate(conn)  # second run must not raise or double-apply
    version = conn.execute("SELECT version FROM schema_version").fetchone()["version"]
    assert version == 3


def _repo(tmp_path: Path) -> BookUnitRepo:
    conn = connect(tmp_path / "colophon.db")
    migrate(conn)
    return BookUnitRepo(conn)


def test_upsert_then_get_round_trips(tmp_path: Path):
    repo = _repo(tmp_path)
    bu = BookUnit.new(source_folder=Path("/ingest/Dune"))
    bu.title = "Dune"
    bu.confidence = 98.0
    repo.upsert(bu)
    fetched = repo.get(bu.id)
    assert fetched is not None
    assert fetched.title == "Dune"
    assert fetched.confidence == 98.0


def test_get_returns_none_for_unknown_id(tmp_path: Path):
    repo = _repo(tmp_path)
    assert repo.get("does-not-exist") is None


def test_upsert_updates_existing_row(tmp_path: Path):
    repo = _repo(tmp_path)
    bu = BookUnit.new(source_folder=Path("/ingest/Dune"))
    repo.upsert(bu)
    bu.state = BookState.READY
    repo.upsert(bu)
    assert repo.get(bu.id).state == BookState.READY
    assert len(repo.list_all()) == 1  # updated, not duplicated


def test_unicode_source_folder_round_trips(tmp_path: Path):
    repo = _repo(tmp_path)
    folder = Path("/ingest/Sòng of Açhilles — Madeline Miller")
    bu = BookUnit.new(source_folder=folder)
    repo.upsert(bu)
    fetched = repo.get(bu.id)
    assert fetched is not None
    assert fetched.id == bu.id
    assert fetched.source_folder == folder


def test_empty_collections_round_trip(tmp_path: Path):
    repo = _repo(tmp_path)
    bu = BookUnit.new(source_folder=Path("/ingest/empty"))
    repo.upsert(bu)
    fetched = repo.get(bu.id)
    assert fetched is not None
    assert fetched.source_files == []
    assert fetched.authors == []
    assert fetched.provenance == {}


def test_created_at_column_preserved_on_update(tmp_path: Path):
    repo = _repo(tmp_path)
    bu = BookUnit.new(source_folder=Path("/ingest/Dune"))
    original_created_at = bu.created_at.isoformat()
    repo.upsert(bu)
    bu.state = BookState.READY
    repo.upsert(bu)
    row = repo.conn.execute(
        "SELECT created_at FROM book_units WHERE id = ?", (bu.id,)
    ).fetchone()
    assert row["created_at"] == original_created_at


def test_runner_applies_only_new_migrations(tmp_path: Path, monkeypatch):
    import colophon.adapters.repository.store as store_mod

    fake_migrations = tmp_path / "migrations"
    fake_migrations.mkdir()
    real_001 = store_mod._MIGRATIONS_DIR / "001_initial.sql"
    (fake_migrations / "001_initial.sql").write_text(
        real_001.read_text(encoding="utf-8"), encoding="utf-8"
    )
    (fake_migrations / "002_extra.sql").write_text(
        "CREATE TABLE extra_table (id INTEGER PRIMARY KEY);", encoding="utf-8"
    )
    monkeypatch.setattr(store_mod, "_MIGRATIONS_DIR", fake_migrations)

    conn = connect(tmp_path / "colophon.db")
    migrate(conn)
    assert conn.execute("SELECT version FROM schema_version").fetchone()["version"] == 2
    assert "extra_table" in _table_names(conn)

    # Drop the table 002 created; a second migrate at version 2 must not re-run it.
    conn.execute("DROP TABLE extra_table")
    conn.commit()
    migrate(conn)
    assert conn.execute("SELECT version FROM schema_version").fetchone()["version"] == 2
    assert "extra_table" not in _table_names(conn)


def test_list_by_state_filters(tmp_path: Path):
    repo = _repo(tmp_path)
    ready = BookUnit.new(source_folder=Path("/ingest/a"))
    ready.state = BookState.READY
    review = BookUnit.new(source_folder=Path("/ingest/b"))
    review.state = BookState.NEEDS_REVIEW
    repo.upsert(ready)
    repo.upsert(review)
    got = repo.list_by_state(BookState.READY)
    assert [b.id for b in got] == [ready.id]


def test_delete_removes_book(tmp_path: Path):
    repo = _repo(tmp_path)
    bu = BookUnit.new(source_folder=Path("/ingest/Gone"))
    repo.upsert(bu)
    assert repo.get(bu.id) is not None
    repo.delete(bu.id)
    assert repo.get(bu.id) is None


def test_delete_missing_id_is_noop(tmp_path: Path):
    repo = _repo(tmp_path)
    repo.delete("does-not-exist")  # must not raise


def _op_repo(tmp_path: Path) -> OperationRepo:
    conn = connect(tmp_path / "colophon.db")
    migrate(conn)
    return OperationRepo(conn)


def test_operations_table_exists(tmp_path: Path):
    conn = connect(tmp_path / "colophon.db")
    migrate(conn)
    assert "operations" in _table_names(conn)


def test_record_and_list_batch_roundtrips(tmp_path: Path):
    repo = _op_repo(tmp_path)
    repo.record(OperationRecord(
        batch_id="b1", book_id="bk", op_type="tag_write", target="/x/01.mp3",
        before='{"title": "old"}', after='{"title": "new"}', outcome="ok",
    ))
    ops = repo.list_batch("b1")
    assert len(ops) == 1
    assert ops[0].target == "/x/01.mp3" and ops[0].before == '{"title": "old"}'
    assert ops[0].outcome == "ok"


def test_latest_batch_id_and_mark_reverted(tmp_path: Path):
    repo = _op_repo(tmp_path)
    repo.record(OperationRecord(batch_id="b1", book_id="bk", op_type="tag_write", target="/x/01.mp3"))
    repo.record(OperationRecord(batch_id="b2", book_id="bk", op_type="tag_write", target="/x/02.mp3"))
    assert repo.latest_batch_id() == "b2"
    repo.mark_reverted("b2")
    assert repo.latest_batch_id() == "b1"


def test_list_all_cache_reflects_upsert_and_delete(tmp_path: Path):
    repo = _repo(tmp_path)
    b = BookUnit.new(source_folder=tmp_path / "x")
    b.title = "First"
    repo.upsert(b)
    assert [x.title for x in repo.list_all()] == ["First"]

    b.title = "Edited"
    repo.upsert(b)  # must invalidate the cache
    assert [x.title for x in repo.list_all()] == ["Edited"]

    repo.delete(b.id)  # must invalidate the cache
    assert repo.list_all() == []


def test_list_all_returns_independent_list(tmp_path: Path):
    repo = _repo(tmp_path)
    repo.upsert(BookUnit.new(source_folder=tmp_path / "x"))
    got = repo.list_all()
    got.append("garbage")  # mutating the returned list must not corrupt the cache
    assert len(repo.list_all()) == 1


def test_list_all_second_call_is_cached(tmp_path: Path):
    repo = _repo(tmp_path)
    repo.upsert(BookUnit.new(source_folder=tmp_path / "x"))
    repo.list_all()  # populate cache
    # Write directly to the DB, bypassing the repo so the cache is NOT invalidated.
    repo.conn.execute("DELETE FROM book_units")
    repo.conn.commit()
    assert len(repo.list_all()) == 1  # served from the cache, not re-read from SQL
