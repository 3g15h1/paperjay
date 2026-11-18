-- AI filing, fact cards, answers feedback.

-- Goes up by one whenever anything search or answers depend on changes (text, title, labels, notes, trash),
-- so in-memory indexes can tell exactly which documents to reload.
ALTER TABLE documents ADD COLUMN rev INTEGER NOT NULL DEFAULT 0;

-- What the AI proposed but didn't apply on its own (new tags), and why a document needs review.
-- {"new_tags": ["Utilities"], "review": ["no company name found"]}
ALTER TABLE documents ADD COLUMN suggestions TEXT NOT NULL DEFAULT '{}';

-- Each document's key facts as read by the AI, checked against its text. Valid while the text is the
-- same (content_hash) and the extractor version and model haven't changed.
CREATE TABLE facts (
    document_id  INTEGER PRIMARY KEY REFERENCES documents(id) ON DELETE CASCADE,
    content_hash TEXT NOT NULL,
    model        TEXT NOT NULL,
    version      INTEGER NOT NULL,
    card         TEXT NOT NULL,              -- JSON: kind, category, vendor, title, summary, total, dates, ids, dropped
    extracted_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now'))
);

-- Thumbs up/down on answers. A later vote or comment with the same id replaces the earlier one.
CREATE TABLE feedback (
    id         TEXT PRIMARY KEY,
    created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now')),
    user_id    INTEGER REFERENCES users(id) ON DELETE SET NULL,
    vote       TEXT NOT NULL CHECK (vote IN ('up', 'down')),
    question   TEXT NOT NULL,
    context_q  TEXT,
    answer     TEXT,
    mode       TEXT,
    document_id INTEGER,
    sources    TEXT NOT NULL DEFAULT '[]',
    missing    TEXT NOT NULL DEFAULT '[]',
    comment    TEXT
);
