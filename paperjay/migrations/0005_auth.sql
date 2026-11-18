-- Sign-in: browser sessions and API tokens. Only hashes of the secrets are stored.
CREATE TABLE sessions (
    token_hash TEXT PRIMARY KEY,
    user_id    INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    created_at REAL NOT NULL,
    last_seen  REAL NOT NULL,
    expires_at REAL NOT NULL,
    user_agent TEXT NOT NULL DEFAULT ''
) WITHOUT ROWID;
CREATE INDEX sessions_user ON sessions(user_id);

-- For scripts, phone shortcuts and calendar apps. scope 'all' acts as the user; 'calendar' may only read
-- the calendar feed (safe to paste into a calendar app's subscription URL).
CREATE TABLE api_tokens (
    id           INTEGER PRIMARY KEY,
    user_id      INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    name         TEXT NOT NULL,
    token_hash   TEXT NOT NULL UNIQUE,
    prefix       TEXT NOT NULL,                -- first characters, so people can tell tokens apart
    scope        TEXT NOT NULL DEFAULT 'all' CHECK (scope IN ('all', 'calendar')),
    created_at   TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now')),
    last_used_at TEXT
);
CREATE INDEX api_tokens_user ON api_tokens(user_id);
