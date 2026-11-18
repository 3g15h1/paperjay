-- Saved searches ("views"): a name and the library filters it stands for.
CREATE TABLE saved_views (
    id         INTEGER PRIMARY KEY,
    name       TEXT NOT NULL,
    params     TEXT NOT NULL,             -- JSON: q, tags, correspondent_id, document_type_id, date_from, date_to, sort
    user_id    INTEGER REFERENCES users(id) ON DELETE CASCADE,
    created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now'))
);
