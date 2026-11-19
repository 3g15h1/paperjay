-- 0009: access keys with chosen permissions (0.6, for AI assistants over MCP). permissions is a
-- comma list of read, create, update, delete; NULL (every key made before) does whatever the
-- account may do. Permissions only narrow what the account's role allows, never widen it.
ALTER TABLE api_tokens ADD COLUMN permissions TEXT;
