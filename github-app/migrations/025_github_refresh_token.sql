-- GitHub Apps with "Expire user authorization tokens" enabled issue a
-- refresh_token alongside the ~8h access_token - without storing it, a
-- session cookie that's still valid for its own 30-day TTL wraps a dead
-- GitHub token with no way to renew it. Nullable: GitHub only returns
-- one when that setting is on, and existing sessions predate this
-- column entirely.
ALTER TABLE sessions ADD COLUMN IF NOT EXISTS github_refresh_token TEXT;
