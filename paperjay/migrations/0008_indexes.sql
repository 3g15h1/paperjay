-- 0008: A–Z index and paper numbers (spec section 5). index_terms holds one row per
-- (term, document): the browsable index (Phase C) is built from these, rewritten per
-- document whenever its profile changes. paper_no is the household's own box number
-- for the paper original (Phase D prints the labels; the column lands here).
CREATE TABLE index_terms (
    kind        TEXT NOT NULL CHECK (kind IN ('company', 'number', 'person', 'topic')),
    norm        TEXT NOT NULL,                       -- lower-case, squashed; sort and prefix key
    term        TEXT NOT NULL,                       -- as shown
    label       TEXT NOT NULL DEFAULT '',            -- "policy number"
    document_id INTEGER NOT NULL REFERENCES documents(id) ON DELETE CASCADE,
    PRIMARY KEY (kind, norm, document_id)
) WITHOUT ROWID;
CREATE INDEX index_terms_doc ON index_terms(document_id);

ALTER TABLE documents ADD COLUMN paper_no INTEGER;
CREATE UNIQUE INDEX documents_paper_no ON documents(paper_no) WHERE paper_no IS NOT NULL;

CREATE TABLE paper_reservations (            -- blank-ahead label sheets (Phase D)
    id         INTEGER PRIMARY KEY,
    first      INTEGER NOT NULL,
    last       INTEGER NOT NULL CHECK (last >= first),
    printed_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now')),
    user_id    INTEGER REFERENCES users(id) ON DELETE SET NULL
);
