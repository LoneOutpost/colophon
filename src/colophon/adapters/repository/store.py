"""SQLite-backed repository: connection, migration runner, and BookUnit CRUD."""

from __future__ import annotations

import sqlite3
import threading
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path

from colophon.core.models import BookState, BookUnit, EditChange, OperationRecord

_MIGRATIONS_DIR = Path(__file__).parent / "migrations"


class _Rows:
    """Materialized statement result that mimics the slice of the sqlite3.Cursor
    API the repos use (`fetchone`/`fetchall`), so callers keep working unchanged
    while `_LockedConnection.execute` reads every row under the lock."""

    def __init__(self, rows: list[sqlite3.Row]) -> None:
        self._rows = rows

    def __iter__(self):
        return iter(self._rows)

    def fetchone(self) -> sqlite3.Row | None:
        return self._rows[0] if self._rows else None

    def fetchall(self) -> list[sqlite3.Row]:
        return self._rows


class _LockedConnection:
    """Serializes statement execution and commits across threads.

    The connection is opened with `check_same_thread=False` so worker threads
    (e.g. the encode/organize job's `asyncio.to_thread` pool) may share it, but
    SQLite gives no isolation between an `execute` on one thread and a `commit`
    on another. This proxy guards `execute`/`executemany`/`executescript`/`commit`
    with a single reentrant lock, executing each statement and materializing its
    rows while the lock is held so a statement and its `fetch*` stay atomic.
    Unguarded attributes (`row_factory`, `close`, ...) pass straight through.
    """

    def __init__(self, conn: sqlite3.Connection) -> None:
        self._conn = conn
        self._lock = threading.RLock()

    def execute(self, sql: str, parameters: tuple = ()) -> _Rows:
        with self._lock:
            return _Rows(self._conn.execute(sql, parameters).fetchall())

    def executemany(self, sql: str, seq_of_parameters: object) -> None:
        with self._lock:
            self._conn.executemany(sql, seq_of_parameters)

    def executescript(self, sql_script: str) -> None:
        with self._lock:
            self._conn.executescript(sql_script)

    def commit(self) -> None:
        with self._lock:
            self._conn.commit()

    def __enter__(self) -> sqlite3.Connection:
        # `with conn:` opens an all-or-nothing transaction; hold the lock for its
        # whole span so a concurrent worker can't interleave or commit mid-way.
        self._lock.acquire()
        return self._conn.__enter__()

    def __exit__(self, exc_type: object, exc: object, tb: object) -> bool:
        try:
            return self._conn.__exit__(exc_type, exc, tb)
        finally:
            self._lock.release()

    def __getattr__(self, name: str) -> object:
        return getattr(self._conn, name)


def connect(db_path: Path) -> sqlite3.Connection:
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(db_path, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    return _LockedConnection(conn)


def _ensure_version_table(conn: sqlite3.Connection) -> None:
    conn.execute("CREATE TABLE IF NOT EXISTS schema_version (version INTEGER NOT NULL)")
    if conn.execute("SELECT version FROM schema_version").fetchone() is None:
        conn.execute("INSERT INTO schema_version (version) VALUES (0)")
        conn.commit()


def migrate(conn: sqlite3.Connection) -> None:
    _ensure_version_table(conn)
    current = int(conn.execute("SELECT version FROM schema_version").fetchone()["version"])
    for f in sorted(_MIGRATIONS_DIR.glob("*.sql")):
        n = int(f.stem.split("_", 1)[0])
        if n <= current:
            continue
        conn.executescript(f.read_text(encoding="utf-8"))
        conn.execute("UPDATE schema_version SET version = ?", (n,))
        conn.commit()


@dataclass
class BookUnitRepo:
    conn: sqlite3.Connection
    # Memoized full-table read keyed by id. A workspace refresh calls list_all()
    # several times (nav tree, list, stats); without this each call re-deserializes
    # every row on the event loop. A *committed* single write updates one entry in
    # place (so the after-an-edit refresh stays warm); an uncommitted (commit=False,
    # bulk) write invalidates instead, so a mid-batch rollback can't leave the cache
    # ahead of the DB — it rebuilds once after the batch commits. The lock guards the
    # cache against worker threads (encode jobs upsert state) racing event-loop reads.
    # Colophon owns its DB, so no external writer can stale it.
    _cache: dict[str, BookUnit] | None = field(default=None, init=False, repr=False)
    _lock: threading.RLock = field(default_factory=threading.RLock, init=False, repr=False)

    def upsert(self, book: BookUnit, commit: bool = True) -> None:
        # The denormalized columns (source_folder, state, confidence, created_at,
        # updated_at) are a read-optimization mirror for querying/sorting; the
        # `data` JSON blob is canonical. created_at is never updated on conflict.
        self.conn.execute(
            """
            INSERT INTO book_units (id, source_folder, state, confidence, data, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(id) DO UPDATE SET
              source_folder = excluded.source_folder,
              state = excluded.state,
              confidence = excluded.confidence,
              data = excluded.data,
              updated_at = excluded.updated_at
            """,
            (
                book.id,
                str(book.source_folder),
                book.state.value,
                book.confidence,
                book.model_dump_json(by_alias=False),
                book.created_at.isoformat(),
                book.updated_at.isoformat(),
            ),
        )
        if commit:
            self.conn.commit()
            with self._lock:
                if self._cache is not None:
                    # store a deep copy so later in-place edits to the caller's
                    # object can't reach the cache without their own write
                    self._cache[book.id] = book.model_copy(deep=True)
        else:
            self._invalidate()

    def get(self, id: str) -> BookUnit | None:
        row = self.conn.execute(
            "SELECT data FROM book_units WHERE id = ?", (id,)
        ).fetchone()
        if row is None:
            return None
        return BookUnit.model_validate_json(row["data"])

    def delete(self, id: str, commit: bool = True) -> None:
        """Remove a book unit. No-op if the id is absent."""
        self.conn.execute("DELETE FROM book_units WHERE id = ?", (id,))
        if commit:
            self.conn.commit()
            with self._lock:
                if self._cache is not None:
                    self._cache.pop(id, None)
        else:
            self._invalidate()

    def _invalidate(self) -> None:
        with self._lock:
            self._cache = None

    def list_all(self) -> list[BookUnit]:
        with self._lock:
            if self._cache is None:
                rows = self.conn.execute("SELECT data FROM book_units").fetchall()
                books = (BookUnit.model_validate_json(r["data"]) for r in rows)
                self._cache = {b.id: b for b in books}
            return list(self._cache.values())  # shallow copy: callers may sort/append freely

    def list_by_state(self, state: BookState) -> list[BookUnit]:
        rows = self.conn.execute(
            "SELECT data FROM book_units WHERE state = ?", (state.value,)
        ).fetchall()
        return [BookUnit.model_validate_json(r["data"]) for r in rows]


@dataclass
class HistoryRepo:
    conn: sqlite3.Connection

    def record(self, batch_id: str, changes: list[EditChange], commit: bool = True) -> None:
        now = datetime.now(UTC).isoformat()
        self.conn.executemany(
            """
            INSERT INTO edit_history (batch_id, book_id, field, old_value, new_value, applied_at)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            [(batch_id, c.book_id, c.field, c.old_value, c.new_value, now) for c in changes],
        )
        if commit:
            self.conn.commit()

    def list_batch(self, batch_id: str) -> list[EditChange]:
        rows = self.conn.execute(
            "SELECT book_id, field, old_value, new_value FROM edit_history "
            "WHERE batch_id = ? ORDER BY id",
            (batch_id,),
        ).fetchall()
        return [
            EditChange(
                book_id=r["book_id"],
                field=r["field"],
                old_value=r["old_value"],
                new_value=r["new_value"],
            )
            for r in rows
        ]

    def latest_batch_id(self) -> str | None:
        row = self.conn.execute(
            "SELECT batch_id FROM edit_history WHERE reverted = 0 ORDER BY id DESC LIMIT 1"
        ).fetchone()
        return row["batch_id"] if row else None

    def mark_reverted(self, batch_id: str) -> None:
        self.conn.execute("UPDATE edit_history SET reverted = 1 WHERE batch_id = ?", (batch_id,))
        self.conn.commit()


@dataclass
class OperationRepo:
    conn: sqlite3.Connection

    def record(self, op: OperationRecord, commit: bool = True) -> None:
        self.conn.execute(
            "INSERT INTO operations "
            "(batch_id, book_id, op_type, target, before, after, applied_at, outcome, detail) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                op.batch_id, op.book_id, op.op_type, op.target, op.before, op.after,
                datetime.now(UTC).isoformat(), op.outcome, op.detail,
            ),
        )
        if commit:
            self.conn.commit()

    def list_batch(self, batch_id: str) -> list[OperationRecord]:
        rows = self.conn.execute(
            "SELECT batch_id, book_id, op_type, target, before, after, outcome, detail "
            "FROM operations WHERE batch_id = ? ORDER BY id",
            (batch_id,),
        ).fetchall()
        return [
            OperationRecord(
                batch_id=r["batch_id"], book_id=r["book_id"], op_type=r["op_type"],
                target=r["target"], before=r["before"], after=r["after"],
                outcome=r["outcome"], detail=r["detail"],
            )
            for r in rows
        ]

    def latest_batch_id(self) -> str | None:
        row = self.conn.execute(
            "SELECT batch_id FROM operations WHERE reverted = 0 ORDER BY id DESC LIMIT 1"
        ).fetchone()
        return row["batch_id"] if row else None

    def mark_reverted(self, batch_id: str) -> None:
        self.conn.execute("UPDATE operations SET reverted = 1 WHERE batch_id = ?", (batch_id,))
        self.conn.commit()
