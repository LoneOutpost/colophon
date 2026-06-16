CREATE TABLE book_units (
    id            TEXT PRIMARY KEY,
    source_folder TEXT NOT NULL,
    state         TEXT NOT NULL,
    confidence    REAL NOT NULL DEFAULT 0,
    data          TEXT NOT NULL,        -- full BookUnit as JSON
    created_at    TEXT NOT NULL,
    updated_at    TEXT NOT NULL
);

CREATE INDEX idx_book_units_state ON book_units (state);
