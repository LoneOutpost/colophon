CREATE TABLE entity_aliases (
    kind TEXT NOT NULL,
    name_key TEXT NOT NULL,
    canonical TEXT NOT NULL,
    PRIMARY KEY (kind, name_key)
);
