-- User-declared entities the classifier should treat as prior evidence. v1 stores only
-- franchises (kind='franchise'); the kind column reserves authors/series for later. Keyed by
-- the normalized name (name_key) so matching is case/spacing/punctuation tolerant; display is
-- the canonical name shown in the UI and used as the classification value.
CREATE TABLE known_entities (
    kind     TEXT NOT NULL,
    name_key TEXT NOT NULL,
    display  TEXT NOT NULL,
    PRIMARY KEY (kind, name_key)
);
