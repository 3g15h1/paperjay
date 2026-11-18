-- 0007: profiles from several sources (spec section 5). Fact cards gain a source
-- ('rules' | 'library' | 'ai'); existing cards are the AI's. Documents gain the
-- merged profile's category and series key; topics and company aliases get tables.
CREATE TABLE facts_v4 (
    document_id  INTEGER NOT NULL REFERENCES documents(id) ON DELETE CASCADE,
    source       TEXT NOT NULL CHECK (source IN ('rules', 'library', 'ai')),
    content_hash TEXT NOT NULL,
    model        TEXT NOT NULL DEFAULT '',
    version      INTEGER NOT NULL,
    card         TEXT NOT NULL,
    extracted_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now')),
    PRIMARY KEY (document_id, source)
) WITHOUT ROWID;
INSERT INTO facts_v4(document_id, source, content_hash, model, version, card, extracted_at)
    SELECT document_id, 'ai', content_hash, model, version, card, extracted_at FROM facts;
DROP TABLE facts;
ALTER TABLE facts_v4 RENAME TO facts;

ALTER TABLE documents ADD COLUMN category TEXT;
ALTER TABLE documents ADD COLUMN series_key TEXT;
ALTER TABLE documents ADD COLUMN simhash INTEGER;
CREATE INDEX documents_category ON documents(category);
CREATE INDEX documents_series ON documents(series_key);

CREATE TABLE document_topics (
    document_id INTEGER NOT NULL REFERENCES documents(id) ON DELETE CASCADE,
    topic       TEXT NOT NULL,
    PRIMARY KEY (document_id, topic)
) WITHOUT ROWID;
CREATE INDEX document_topics_topic ON document_topics(topic);

CREATE TABLE correspondent_aliases (
    id               INTEGER PRIMARY KEY,
    correspondent_id INTEGER NOT NULL REFERENCES correspondents(id) ON DELETE CASCADE,
    alias            TEXT NOT NULL UNIQUE COLLATE NOCASE,
    source           TEXT NOT NULL CHECK (source IN ('person', 'merge', 'sender')),
    created_at       TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now'))
);

ALTER TABLE uploads ADD COLUMN sender TEXT;
