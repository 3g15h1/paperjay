-- Every upload attempt and what happened to it, including the ones that didn't become a document
-- (duplicates, unsupported files). Its id is the "task id" the Paperless-compatible API hands out.
CREATE TABLE uploads (
    id           TEXT PRIMARY KEY,
    created_at   TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now')),
    source       TEXT NOT NULL,            -- web, api, paperless-api, consume
    filename     TEXT NOT NULL,
    user_id      INTEGER REFERENCES users(id) ON DELETE SET NULL,
    outcome      TEXT NOT NULL CHECK (outcome IN ('accepted', 'duplicate', 'rejected')),
    document_id  INTEGER REFERENCES documents(id) ON DELETE SET NULL,
    duplicate_of INTEGER,
    message      TEXT
);
CREATE INDEX uploads_created ON uploads(created_at);
