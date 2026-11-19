-- 0013: the household's own fields (0.7.8): a policy number, a licence plate, a warranty card number…
-- A field may fill itself from what PaperJay read (fill: '' nothing, 'id:<words>' the number whose label has
-- those words, 'date:<label>' a date such as expires, 'total'). document_fields holds the values a person
-- typed; filled values are worked out when shown and never stored, so they follow the document's text.
CREATE TABLE custom_fields (
    id         INTEGER PRIMARY KEY,
    name       TEXT NOT NULL UNIQUE COLLATE NOCASE,
    kind       TEXT NOT NULL DEFAULT 'text' CHECK (kind IN ('text', 'number', 'money', 'date')),
    fill       TEXT NOT NULL DEFAULT '',
    position   INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now'))
);

CREATE TABLE document_fields (
    document_id INTEGER NOT NULL REFERENCES documents(id) ON DELETE CASCADE,
    field_id    INTEGER NOT NULL REFERENCES custom_fields(id) ON DELETE CASCADE,
    value       TEXT NOT NULL,             -- '' = "none" (a person cleared a filled value on purpose)
    PRIMARY KEY (document_id, field_id)
) WITHOUT ROWID;
CREATE INDEX document_fields_field ON document_fields(field_id, value);
