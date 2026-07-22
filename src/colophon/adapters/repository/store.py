"""SQLite-backed repository: connection, migration runner, and BookUnit CRUD."""

from __future__ import annotations

import json
import sqlite3
import threading
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING

from colophon.adapters.realdebrid import RdTorrentInfo, RdUnrestrictedLink
from colophon.core.graph_records import EdgeRecord, NodeRecord
from colophon.core.models import BookState, BookUnit, EditChange, NodeOverride, OperationRecord

if TYPE_CHECKING:
    from colophon.core.library_graph import LibraryGraph

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
    _generation: int = field(default=0, init=False, repr=False)

    @property
    def generation(self) -> int:
        """Monotonic write counter, bumped on every upsert/delete/invalidate. A derived-data
        cache (e.g. the controller's library tree or autocomplete lists) can memoize against it:
        an unchanged generation means the book set is unchanged, so no rebuild is needed."""
        return self._generation

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
                self._generation += 1
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
                self._generation += 1
                if self._cache is not None:
                    self._cache.pop(id, None)
        else:
            self._invalidate()

    def _invalidate(self) -> None:
        with self._lock:
            self._cache = None
            self._generation += 1

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

    def count_by_state(self) -> dict[str, int]:
        """Per-state row counts from the denormalized `state` column — no JSON parsing, so it's
        cheap enough to poll for the header pipeline counts."""
        rows = self.conn.execute(
            "SELECT state, COUNT(*) AS n FROM book_units GROUP BY state"
        ).fetchall()
        return {r["state"]: r["n"] for r in rows}

    def ids_in_folder(self, folder: Path) -> set[str]:
        """Ids of every persisted book whose source_folder equals `folder`."""
        rows = self.conn.execute(
            "SELECT id FROM book_units WHERE source_folder = ?", (str(folder),)
        ).fetchall()
        return {r["id"] for r in rows}


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

    def delete_for_book(self, book_id: str, commit: bool = True) -> None:
        """Remove all edit-history rows for a deleted book."""
        self.conn.execute("DELETE FROM edit_history WHERE book_id = ?", (book_id,))
        if commit:
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

    def delete_for_book(self, book_id: str, commit: bool = True) -> None:
        """Remove all operation rows for a deleted book."""
        self.conn.execute("DELETE FROM operations WHERE book_id = ?", (book_id,))
        if commit:
            self.conn.commit()


@dataclass
class NodeOverrideRepo:
    """Persisted manual node classifications, keyed by absolute folder path."""

    conn: sqlite3.Connection

    def set(self, path: str, kind: str, value: str | None = None) -> None:
        self.conn.execute(
            "INSERT INTO node_overrides (path, kind, value) VALUES (?, ?, ?) "
            "ON CONFLICT(path) DO UPDATE SET kind = excluded.kind, value = excluded.value",
            (path, kind, value),
        )
        self.conn.commit()

    def set_many(self, rows: list[tuple[str, str, str | None]]) -> None:
        """Upsert many (path, kind, value) overrides under a single commit (a bulk cohort
        confirm can touch hundreds of nodes)."""
        for path, kind, value in rows:
            self.conn.execute(
                "INSERT INTO node_overrides (path, kind, value) VALUES (?, ?, ?) "
                "ON CONFLICT(path) DO UPDATE SET kind = excluded.kind, value = excluded.value",
                (path, kind, value),
            )
        self.conn.commit()

    def clear(self, path: str) -> None:
        self.conn.execute("DELETE FROM node_overrides WHERE path = ?", (path,))
        self.conn.commit()

    def get(self, path: str) -> NodeOverride | None:
        row = self.conn.execute(
            "SELECT kind, value FROM node_overrides WHERE path = ?", (path,)
        ).fetchone()
        return NodeOverride(kind=row["kind"], value=row["value"]) if row else None

    def all(self) -> dict[str, NodeOverride]:
        rows = self.conn.execute("SELECT path, kind, value FROM node_overrides").fetchall()
        return {r["path"]: NodeOverride(kind=r["kind"], value=r["value"]) for r in rows}


@dataclass
class GroupingOverrideRepo:
    """Persisted 'this folder's audio is one book' overrides, keyed by absolute folder path.
    Kept separate from node-kind overrides so reclassifying a folder to Book never silently
    regroups its files — only an explicit Combine sets this. `mode` is 'single' for v1."""

    conn: sqlite3.Connection

    def set_single(self, path: str, snapshot: str | None = None) -> None:
        """Force `path` to a single book. `snapshot` is the JSON of the books this Combine
        replaced, kept so an Uncombine can restore them exactly."""
        self.conn.execute(
            "INSERT INTO grouping_overrides (path, mode, snapshot) VALUES (?, 'single', ?) "
            "ON CONFLICT(path) DO UPDATE SET mode = excluded.mode, snapshot = excluded.snapshot",
            (path, snapshot),
        )
        self.conn.commit()

    def snapshot(self, path: str) -> str | None:
        row = self.conn.execute(
            "SELECT snapshot FROM grouping_overrides WHERE path = ?", (path,)
        ).fetchone()
        return row["snapshot"] if row else None

    def clear(self, path: str) -> None:
        self.conn.execute("DELETE FROM grouping_overrides WHERE path = ?", (path,))
        self.conn.commit()

    def is_single(self, path: str) -> bool:
        row = self.conn.execute(
            "SELECT 1 FROM grouping_overrides WHERE path = ? AND mode = 'single'", (path,)
        ).fetchone()
        return row is not None

    def single_folders(self) -> frozenset[str]:
        """Every folder path forced to a single book (mode='single')."""
        rows = self.conn.execute(
            "SELECT path FROM grouping_overrides WHERE mode = 'single'"
        ).fetchall()
        return frozenset(r["path"] for r in rows)

    def set_partition(self, path: str, groups: list[list[str]]) -> None:
        """Force `path` to a fixed split: `groups` is the folder's file->book grouping (each inner
        list is one book's file names). Stored as JSON in `snapshot` under mode='partition'. Replaces
        any existing override on the folder (a folder has one override row), so this and `set_single`
        are mutually exclusive."""
        self.conn.execute(
            "INSERT INTO grouping_overrides (path, mode, snapshot) VALUES (?, 'partition', ?) "
            "ON CONFLICT(path) DO UPDATE SET mode = excluded.mode, snapshot = excluded.snapshot",
            (path, json.dumps(groups)),
        )
        self.conn.commit()

    def partition(self, path: str) -> list[list[str]] | None:
        """The stored file->book grouping for `path`, or None if it is not partitioned."""
        row = self.conn.execute(
            "SELECT snapshot FROM grouping_overrides WHERE path = ? AND mode = 'partition'", (path,)
        ).fetchone()
        return json.loads(row["snapshot"]) if row and row["snapshot"] else None

    def partitioned_folders(self) -> dict[str, list[list[str]]]:
        """Every partitioned folder mapped to its file->book grouping."""
        rows = self.conn.execute(
            "SELECT path, snapshot FROM grouping_overrides WHERE mode = 'partition'"
        ).fetchall()
        return {r["path"]: json.loads(r["snapshot"]) for r in rows if r["snapshot"]}


@dataclass
class EntityAliasRepo:
    """Persisted entity merge/rename facts, keyed by (kind, name_key).

    The entity-shaped analog of NodeOverrideRepo: where node overrides are keyed by
    a directory's path, entity nodes have no path, so an alias is keyed by the
    normalized name (`_name_key`) of the source entity. The value is the canonical
    display name. Merge (source -> existing name) and rename (source -> new name)
    are the same row; clearing it reverts to the auto-derived entity. Callers pass
    an already-normalized `name_key`."""

    conn: sqlite3.Connection
    _generation: int = field(default=0, init=False, repr=False)

    @property
    def generation(self) -> int:
        """Monotonic write counter, bumped on every set/clear. Aliases resolve at read time, so a
        derived cache (the controller's library tree) has no other signal that a merge/rename ran."""
        return self._generation

    def set(self, kind: str, name_key: str, canonical: str) -> None:
        self.conn.execute(
            "INSERT INTO entity_aliases (kind, name_key, canonical) VALUES (?, ?, ?) "
            "ON CONFLICT(kind, name_key) DO UPDATE SET canonical = excluded.canonical",
            (kind, name_key, canonical),
        )
        self.conn.commit()
        self._generation += 1

    def clear(self, kind: str, name_key: str) -> None:
        self.conn.execute(
            "DELETE FROM entity_aliases WHERE kind = ? AND name_key = ?", (kind, name_key)
        )
        self.conn.commit()
        self._generation += 1

    def all(self) -> dict[tuple[str, str], str]:
        rows = self.conn.execute(
            "SELECT kind, name_key, canonical FROM entity_aliases"
        ).fetchall()
        return {(r["kind"], r["name_key"]): r["canonical"] for r in rows}


@dataclass
class KnownFranchiseRepo:
    """User-declared franchises the classifier should treat as a franchise tier rather than
    infer as an author. Rows live in `known_entities` with kind='franchise', keyed by the
    normalized name (`_name_key`) so matching tolerates case/spacing/punctuation. The display
    value is the canonical name shown in the UI and used as the classification value."""

    conn: sqlite3.Connection

    def add(self, name: str) -> None:
        from colophon.core.graph_resolve import _name_key
        self.conn.execute(
            "INSERT INTO known_entities (kind, name_key, display) VALUES ('franchise', ?, ?) "
            "ON CONFLICT(kind, name_key) DO UPDATE SET display = excluded.display",
            (_name_key(name), name),
        )
        self.conn.commit()

    def remove(self, name: str) -> None:
        from colophon.core.graph_resolve import _name_key
        self.conn.execute(
            "DELETE FROM known_entities WHERE kind = 'franchise' AND name_key = ?",
            (_name_key(name),),
        )
        self.conn.commit()

    def all(self) -> dict[str, str]:
        """The user-declared franchises only (`name_key -> display`)."""
        rows = self.conn.execute(
            "SELECT name_key, display FROM known_entities WHERE kind = 'franchise'"
        ).fetchall()
        return {r["name_key"]: r["display"] for r in rows}

    def active(self) -> dict[str, str]:
        """Every franchise the classifier should recognize: the built-in seeds (see
        `franchise_seeds`) plus the user-declared ones, with a user declaration overriding a
        seed's display on a shared key. This is what the scan reads; `all` stays 'what the user
        declared' so the Manage UI lists only removable, user-owned entries."""
        from colophon.core.franchise_seeds import default_franchises
        return {**default_franchises(), **self.all()}


@dataclass
class GraphStore:
    """Persisted property-graph: generic nodes + typed edges. Slice 1 holds the
    structural layer (directory/file/book nodes, contains/owns edges). Replace-by-root:
    a full scan rebuilds a root's whole subgraph, so a re-scan replaces it wholesale.
    Scan roots are assumed disjoint (a node belongs to one root)."""

    conn: sqlite3.Connection

    def replace_subgraph(
        self, root: Path, nodes: list[NodeRecord], edges: list[EdgeRecord], commit: bool = True
    ) -> None:
        """Atomically replace `root`'s whole subgraph: delete its nodes/edges, insert the
        new set. With `commit=True` the delete+insert run in one all-or-nothing
        transaction (rolled back if any insert fails). With `commit=False` the caller owns
        the surrounding transaction and its commit/rollback — the statements join it."""
        r = str(root)
        # `nodes.id` is a global primary key, but physical (directory/file/book) ids are not
        # root-scoped: the same id can linger under a different root string — a stale/renamed scan
        # path, or an overlapping config. Dedup the incoming batch (keep first) and reclaim any of
        # its ids still owned by another root, so the insert below can't collide on `nodes.id`.
        seen: set[str] = set()
        unique_nodes = [n for n in nodes if not (n.id in seen or seen.add(n.id))]

        def _write() -> None:
            self.conn.execute("DELETE FROM nodes WHERE root = ?", (r,))
            self.conn.execute("DELETE FROM edges WHERE root = ?", (r,))
            if seen:
                self.conn.executemany(
                    "DELETE FROM nodes WHERE id = ? AND root != ?", [(nid, r) for nid in seen])
            self.conn.executemany(
                "INSERT INTO nodes (id, physical, semantic, root, attrs) VALUES (?, ?, ?, ?, ?)",
                [(n.id, n.physical, n.semantic, n.root, json.dumps(n.attrs)) for n in unique_nodes],
            )
            self.conn.executemany(
                "INSERT INTO edges (src, kind, dst, root, props) VALUES (?, ?, ?, ?, ?)",
                [(e.src, e.kind, e.dst, e.root, json.dumps(e.props)) for e in edges],
            )

        if commit:
            with self.conn:  # one transaction, lock held throughout; commit/rollback on exit
                _write()
        else:
            _write()

    def nodes_for(self, root: Path) -> list[NodeRecord]:
        rows = self.conn.execute(
            "SELECT id, physical, semantic, root, attrs FROM nodes WHERE root = ?", (str(root),)
        ).fetchall()
        return [
            NodeRecord(id=r["id"], physical=r["physical"], semantic=r["semantic"],
                       root=r["root"], attrs=json.loads(r["attrs"]))
            for r in rows
        ]

    def edges_for(self, root: Path) -> list[EdgeRecord]:
        rows = self.conn.execute(
            "SELECT src, kind, dst, root, props FROM edges WHERE root = ?", (str(root),)
        ).fetchall()
        return [
            EdgeRecord(src=r["src"], kind=r["kind"], dst=r["dst"], root=r["root"],
                       props=json.loads(r["props"]))
            for r in rows
        ]

    def load_all(self) -> tuple[list[NodeRecord], list[EdgeRecord]]:
        """Every node and edge across all roots — the whole persisted graph, for
        materializing the in-memory LibraryGraph at startup."""
        node_rows = self.conn.execute(
            "SELECT id, physical, semantic, root, attrs FROM nodes"
        ).fetchall()
        edge_rows = self.conn.execute(
            "SELECT src, kind, dst, root, props FROM edges"
        ).fetchall()
        nodes = [
            NodeRecord(id=r["id"], physical=r["physical"], semantic=r["semantic"],
                       root=r["root"], attrs=json.loads(r["attrs"]))
            for r in node_rows
        ]
        edges = [
            EdgeRecord(src=r["src"], kind=r["kind"], dst=r["dst"], root=r["root"],
                       props=json.loads(r["props"]))
            for r in edge_rows
        ]
        return nodes, edges


@dataclass
class RdCacheRepo:
    """Persisted Real-Debrid responses: completed-torrent info and per-link unrestrict
    results, so repeat picks and Acquire page loads don't re-hit the API. Freshness is
    lazy — a value is refetched only on an explicit force or when a download fails and
    re-resolves the one link. Link rows carry no torrent id; a torrent's links are read
    from its own cached `links[]` at eviction time."""

    conn: sqlite3.Connection

    def get_torrent_info(self, tid: str) -> RdTorrentInfo | None:
        row = self.conn.execute(
            "SELECT info_json FROM rd_torrent_cache WHERE torrent_id = ?", (tid,)
        ).fetchone()
        return RdTorrentInfo.model_validate_json(row["info_json"]) if row else None

    def put_torrent_info(self, info: RdTorrentInfo) -> None:
        self.conn.execute(
            "INSERT INTO rd_torrent_cache (torrent_id, filename, status, bytes, info_json, cached_at) "
            "VALUES (?, ?, ?, ?, ?, ?) "
            "ON CONFLICT(torrent_id) DO UPDATE SET filename=excluded.filename, "
            "status=excluded.status, bytes=excluded.bytes, info_json=excluded.info_json, "
            "cached_at=excluded.cached_at",
            (info.id, info.filename, info.status, info.bytes,
             info.model_dump_json(), datetime.now(UTC).isoformat()),
        )
        self.conn.commit()

    def get_link(self, link: str) -> RdUnrestrictedLink | None:
        row = self.conn.execute(
            "SELECT filename, filesize, mime_type, download_url FROM rd_link_cache WHERE link = ?",
            (link,),
        ).fetchone()
        if row is None:
            return None
        return RdUnrestrictedLink(
            filename=row["filename"], filesize=row["filesize"],
            mime_type=row["mime_type"], download=row["download_url"])

    def put_link(self, link: str, unr: RdUnrestrictedLink) -> None:
        self.conn.execute(
            "INSERT INTO rd_link_cache (link, filename, filesize, mime_type, download_url, cached_at) "
            "VALUES (?, ?, ?, ?, ?, ?) "
            "ON CONFLICT(link) DO UPDATE SET filename=excluded.filename, filesize=excluded.filesize, "
            "mime_type=excluded.mime_type, download_url=excluded.download_url, cached_at=excluded.cached_at",
            (link, unr.filename, unr.filesize, unr.mime_type, unr.download,
             datetime.now(UTC).isoformat()),
        )
        self.conn.commit()

    def evict_torrent(self, tid: str) -> None:
        info = self.get_torrent_info(tid)
        if info is not None and info.links:
            placeholders = ",".join("?" for _ in info.links)
            self.conn.execute(
                f"DELETE FROM rd_link_cache WHERE link IN ({placeholders})", tuple(info.links))
        self.conn.execute("DELETE FROM rd_torrent_cache WHERE torrent_id = ?", (tid,))
        self.conn.commit()

    def evict_torrents(self, keep_ids: set[str]) -> None:
        rows = self.conn.execute("SELECT torrent_id FROM rd_torrent_cache").fetchall()
        for r in rows:
            if r["torrent_id"] not in keep_ids:
                self.evict_torrent(r["torrent_id"])


def save_graph(store: GraphStore, graph: LibraryGraph) -> None:
    """Persist the whole in-memory graph back to the store, replacing each root's
    subgraph. (No production mutator yet — Slice 2 wires write-through; this is the
    save half of load/save, exercised by round-trip tests in a later task.)"""
    roots = {n.root for n in graph.nodes.values()} | {e.root for e in graph.edges}
    for root in roots:
        nodes = [n for n in graph.nodes.values() if n.root == root]
        edges = [e for e in graph.edges if e.root == root]
        store.replace_subgraph(Path(root), nodes, edges)
