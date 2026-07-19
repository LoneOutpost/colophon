CREATE TABLE rd_torrent_cache (
    torrent_id TEXT PRIMARY KEY,
    filename   TEXT NOT NULL,
    status     TEXT NOT NULL,
    bytes      INTEGER NOT NULL DEFAULT 0,
    info_json  TEXT NOT NULL,
    cached_at  TEXT NOT NULL
);

CREATE TABLE rd_link_cache (
    link         TEXT PRIMARY KEY,
    filename     TEXT NOT NULL,
    filesize     INTEGER NOT NULL DEFAULT 0,
    mime_type    TEXT,
    download_url TEXT NOT NULL,
    cached_at    TEXT NOT NULL
);
