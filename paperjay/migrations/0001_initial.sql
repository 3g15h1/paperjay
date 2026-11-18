-- PaperJay schema v1.
-- Times people see are ISO 8601 UTC text; job scheduling times are epoch seconds (REAL).

CREATE TABLE users (
    id            INTEGER PRIMARY KEY,
    username      TEXT NOT NULL UNIQUE COLLATE NOCASE,
    display_name  TEXT NOT NULL DEFAULT '',
    password_hash TEXT,                       -- NULL: the account can't sign in (e.g. an imported owner)
    role          TEXT NOT NULL CHECK (role IN ('admin', 'member', 'readonly')),
    disabled      INTEGER NOT NULL DEFAULT 0,
    created_at    TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now'))
);

-- The three kinds of label a document can have. Same shape, so the library code shares one helper.
CREATE TABLE correspondents (
    id         INTEGER PRIMARY KEY,
    name       TEXT NOT NULL UNIQUE COLLATE NOCASE,
    created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now'))
);

CREATE TABLE document_types (
    id         INTEGER PRIMARY KEY,
    name       TEXT NOT NULL UNIQUE COLLATE NOCASE,
    created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now'))
);

CREATE TABLE tags (
    id         INTEGER PRIMARY KEY,
    name       TEXT NOT NULL UNIQUE COLLATE NOCASE,
    color      TEXT NOT NULL DEFAULT '#6b7280',
    created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now'))
);

CREATE TABLE documents (
    id               INTEGER PRIMARY KEY,
    title            TEXT NOT NULL,
    doc_date         TEXT,                    -- the date the document is about, YYYY-MM-DD
    added_at         TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now')),
    modified_at      TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now')),
    correspondent_id INTEGER REFERENCES correspondents(id) ON DELETE SET NULL,
    document_type_id INTEGER REFERENCES document_types(id) ON DELETE SET NULL,
    owner_id         INTEGER REFERENCES users(id) ON DELETE SET NULL,
    checksum         TEXT NOT NULL UNIQUE,    -- sha256 of the original file; also its name in the file store
    original_name    TEXT NOT NULL,
    original_ext     TEXT NOT NULL,
    mime_type        TEXT NOT NULL,
    size             INTEGER NOT NULL,
    archive_checksum TEXT,                    -- sha256 of the searchable PDF; NULL until OCR has made one
    page_count       INTEGER,
    content          TEXT NOT NULL DEFAULT '',
    status           TEXT NOT NULL DEFAULT 'pending'
                     CHECK (status IN ('pending', 'processing', 'ready', 'failed')),
    error            TEXT,
    needs_review     INTEGER NOT NULL DEFAULT 0,
    ai_fields        TEXT NOT NULL DEFAULT '[]', -- JSON list of the fields last set by AI, not a person
    deleted_at       TEXT                     -- set while the document is in the trash
);
CREATE INDEX documents_doc_date ON documents(doc_date);
CREATE INDEX documents_added_at ON documents(added_at);
CREATE INDEX documents_correspondent ON documents(correspondent_id);
CREATE INDEX documents_type ON documents(document_type_id);
CREATE INDEX documents_deleted_at ON documents(deleted_at);
CREATE INDEX documents_status ON documents(status);

CREATE TABLE document_tags (
    document_id INTEGER NOT NULL REFERENCES documents(id) ON DELETE CASCADE,
    tag_id      INTEGER NOT NULL REFERENCES tags(id) ON DELETE CASCADE,
    PRIMARY KEY (document_id, tag_id)
) WITHOUT ROWID;
CREATE INDEX document_tags_tag ON document_tags(tag_id);

CREATE TABLE notes (
    id          INTEGER PRIMARY KEY,
    document_id INTEGER NOT NULL REFERENCES documents(id) ON DELETE CASCADE,
    user_id     INTEGER REFERENCES users(id) ON DELETE SET NULL,
    body        TEXT NOT NULL,
    created_at  TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now'))
);
CREATE INDEX notes_document ON notes(document_id);

-- Full-text index; rowid = documents.id. Kept in sync by library.py rather than triggers, because
-- `meta` (tag, correspondent and type names) comes from other tables. Trashed documents are left out.
CREATE VIRTUAL TABLE documents_fts USING fts5(
    title, meta, content,
    tokenize = 'porter unicode61 remove_diacritics 2'
);

-- Passage embeddings for search by meaning: float32 vectors, one row per passage.
CREATE TABLE embeddings (
    document_id INTEGER NOT NULL REFERENCES documents(id) ON DELETE CASCADE,
    model       TEXT NOT NULL,
    chunk       INTEGER NOT NULL,
    char_start  INTEGER NOT NULL,
    char_end    INTEGER NOT NULL,
    vector      BLOB NOT NULL,
    PRIMARY KEY (document_id, model, chunk)
) WITHOUT ROWID;

CREATE TABLE jobs (
    id           INTEGER PRIMARY KEY,
    kind         TEXT NOT NULL,
    document_id  INTEGER REFERENCES documents(id) ON DELETE CASCADE,
    payload      TEXT NOT NULL DEFAULT '{}',
    status       TEXT NOT NULL DEFAULT 'queued' CHECK (status IN ('queued', 'running', 'done', 'failed')),
    priority     INTEGER NOT NULL DEFAULT 100, -- lower runs first
    attempts     INTEGER NOT NULL DEFAULT 0,
    max_attempts INTEGER NOT NULL DEFAULT 3,
    run_after    REAL NOT NULL,
    created_at   REAL NOT NULL,
    started_at   REAL,
    finished_at  REAL,
    worker       TEXT,
    error        TEXT
);
CREATE INDEX jobs_ready ON jobs(status, priority, run_after, id);
CREATE INDEX jobs_document ON jobs(document_id);

-- Who changed what. No foreign key on document_id, so the history outlives a purged document.
CREATE TABLE audit_log (
    id          INTEGER PRIMARY KEY,
    at          TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now')),
    user_id     INTEGER REFERENCES users(id) ON DELETE SET NULL,
    action      TEXT NOT NULL,
    document_id INTEGER,
    detail      TEXT NOT NULL DEFAULT '{}'
);
CREATE INDEX audit_log_document ON audit_log(document_id);

-- Settings changed in the app (JSON values), plus internal counters.
CREATE TABLE settings (
    key   TEXT PRIMARY KEY,
    value TEXT NOT NULL
) WITHOUT ROWID;
