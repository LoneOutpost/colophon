CREATE TABLE operations (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    batch_id   TEXT NOT NULL,
    book_id    TEXT NOT NULL,
    op_type    TEXT NOT NULL,
    target     TEXT NOT NULL,
    before     TEXT,
    after      TEXT,
    applied_at TEXT NOT NULL,
    outcome    TEXT NOT NULL,
    detail     TEXT,
    reverted   INTEGER NOT NULL DEFAULT 0
);

CREATE INDEX idx_operations_batch ON operations (batch_id);
