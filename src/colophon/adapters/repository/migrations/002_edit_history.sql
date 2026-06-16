CREATE TABLE edit_history (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    batch_id   TEXT NOT NULL,
    book_id    TEXT NOT NULL,
    field      TEXT NOT NULL,
    old_value  TEXT,
    new_value  TEXT,
    applied_at TEXT NOT NULL,
    reverted   INTEGER NOT NULL DEFAULT 0
);

CREATE INDEX idx_edit_history_batch ON edit_history (batch_id);
