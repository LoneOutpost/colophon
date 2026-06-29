CREATE TABLE nodes (
    id        TEXT PRIMARY KEY,
    physical  TEXT,
    semantic  TEXT,
    root      TEXT NOT NULL,
    attrs     TEXT NOT NULL
);
CREATE INDEX idx_nodes_root ON nodes(root);

CREATE TABLE edges (
    src   TEXT NOT NULL,
    kind  TEXT NOT NULL,
    dst   TEXT NOT NULL,
    root  TEXT NOT NULL,
    props TEXT NOT NULL,
    PRIMARY KEY (src, kind, dst)
);
CREATE INDEX idx_edges_src ON edges(src);
CREATE INDEX idx_edges_dst ON edges(dst);
CREATE INDEX idx_edges_root ON edges(root);
