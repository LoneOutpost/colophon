-- Small key/value scratch for facts that must outlive a process: markers recording that an
-- expensive whole-library pass already ran against a given state of the catalog, so a restart
-- does not repeat work whose inputs have not moved.
CREATE TABLE app_state (
    key   TEXT PRIMARY KEY,
    value TEXT NOT NULL
);
