from pathlib import Path

from colophon.adapters.repository.store import BookUnitRepo, connect, migrate
from colophon.core.models import BookState, BookUnit


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
    assert version == 2


def test_migrate_is_idempotent(tmp_path: Path):
    conn = connect(tmp_path / "colophon.db")
    migrate(conn)
    migrate(conn)  # second run must not raise or double-apply
    version = conn.execute("SELECT version FROM schema_version").fetchone()["version"]
    assert version == 2


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
