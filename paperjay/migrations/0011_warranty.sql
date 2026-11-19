-- 0011: warranties (0.7.2). warranty_until is a person's own setting for one document: YYYY-MM-DD,
-- '9999-12-31' for a lifetime warranty, or 'none' for no warranty. NULL: read it from the text (warranty.py).
ALTER TABLE documents ADD COLUMN warranty_until TEXT;
