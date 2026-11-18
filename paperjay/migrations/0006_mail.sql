-- Mailboxes PaperJay checks for documents (attachments of emails), and the emails it has already taken.
CREATE TABLE mail_accounts (
    id            INTEGER PRIMARY KEY,
    name          TEXT NOT NULL,
    host          TEXT NOT NULL,
    port          INTEGER NOT NULL DEFAULT 993,
    security      TEXT NOT NULL DEFAULT 'ssl' CHECK (security IN ('ssl', 'starttls', 'none')),
    username      TEXT NOT NULL,
    password      TEXT NOT NULL,             -- an app password; never sent back to the browser
    folder        TEXT NOT NULL DEFAULT 'INBOX',
    from_filter   TEXT NOT NULL DEFAULT '',  -- "bills@acme.com, fastnet.net": only emails from these ('' = all)
    subject_filter TEXT NOT NULL DEFAULT '', -- "invoice, statement": subject has one of these words ('' = all)
    unread_only   INTEGER NOT NULL DEFAULT 0,
    look_back_days INTEGER NOT NULL DEFAULT 30,
    after         TEXT NOT NULL DEFAULT 'keep' CHECK (after IN ('keep', 'read', 'move')),
    move_to       TEXT NOT NULL DEFAULT '',
    enabled       INTEGER NOT NULL DEFAULT 1,
    user_id       INTEGER REFERENCES users(id) ON DELETE SET NULL,   -- documents are added as this person
    created_at    TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now')),
    last_run_at   REAL,
    last_ok       INTEGER,
    last_message  TEXT,
    last_added    INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE mail_seen (
    account_id  INTEGER NOT NULL REFERENCES mail_accounts(id) ON DELETE CASCADE,
    uidvalidity INTEGER NOT NULL,
    uid         INTEGER NOT NULL,
    seen_at     REAL NOT NULL,
    PRIMARY KEY (account_id, uidvalidity, uid)
) WITHOUT ROWID;
