-- 0014: two-step sign-in (0.8.0): a code from an authenticator app after the password. totp_secret is set
-- once it's turned on (totp_pending while it's being set up); totp_last_step stops a code being used twice;
-- recovery_codes holds sha256 hashes of the one-time codes for a lost phone.
ALTER TABLE users ADD COLUMN totp_secret TEXT;
ALTER TABLE users ADD COLUMN totp_pending TEXT;
ALTER TABLE users ADD COLUMN totp_last_step INTEGER NOT NULL DEFAULT 0;
ALTER TABLE users ADD COLUMN recovery_codes TEXT NOT NULL DEFAULT '[]';
