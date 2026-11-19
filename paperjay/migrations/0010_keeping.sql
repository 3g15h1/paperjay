-- 0010: how long to keep the paper (0.7.1). keep_until is a person's own choice for one document
-- (YYYY-MM-DD; '9999-12-31' = keep it for good); NULL follows the keep rules in Settings. When the paper
-- is shredded, shredded_at is set and its paper number moves to shredded_paper_no, so the shelf number is
-- free on the label but never handed out again. The digital copy always stays.
ALTER TABLE documents ADD COLUMN keep_until TEXT;
ALTER TABLE documents ADD COLUMN shredded_at TEXT;
ALTER TABLE documents ADD COLUMN shredded_paper_no INTEGER;
