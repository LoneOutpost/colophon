"""SQLite-backed repository: connection, migration runner, and BookUnit CRUD."""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from colophon.core.models import BookState, BookUnit, EditChange

_MIGRATIONS_DIR = Path(__file__).parent / "migrations"


def connect(db_path: Path) -> sqlite3.Connection:
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(db_path, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    return conn


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

    def list_all(self) -> list[BookUnit]:
        rows = self.conn.execute("SELECT data FROM book_units").fetchall()
        return [BookUnit.model_validate_json(r["data"]) for r in rows]

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
