-- 0012: an email itself as a PDF document (0.7.7): never (as before), when it has no document attached
-- (a bill in the body of the email), or always (with its attachments as their own documents).
ALTER TABLE mail_accounts ADD COLUMN email_pdf TEXT NOT NULL DEFAULT 'never'
    CHECK (email_pdf IN ('never', 'no_attachment', 'always'));
