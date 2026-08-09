-- Captured via the GitHub OAuth user:email scope on every login (see
-- auth.py's callback), keyed by github_login rather than stored on
-- sessions - sessions expire and get pruned by run_session_cleanup_job,
-- but transactional email (welcome, payment-failed, weekly digest) needs
-- an address that outlives any one session, and self-heals if someone's
-- GitHub email changes since it's upserted on every login.
CREATE TABLE IF NOT EXISTS github_user_emails (
    github_login  TEXT PRIMARY KEY,
    email         TEXT NOT NULL,
    updated_at    TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- Paddle webhooks are at-least-once delivery, so dedupe_key (e.g.
-- "payment_failed:{paddle_event_id}") has a UNIQUE constraint - a
-- retried webhook enqueuing the same email job twice can't double-send.
-- The row is only inserted after a successful Resend call (not before,
-- as a "claim"), so a transient send failure doesn't permanently block
-- a legitimate retry. Doubles as the send log; resend_message_id lets a
-- future delivery-status webhook (bounce/complaint) correlate back to
-- what was actually sent.
CREATE TABLE IF NOT EXISTS sent_emails (
    id                 BIGSERIAL PRIMARY KEY,
    dedupe_key         TEXT NOT NULL UNIQUE,
    template_name      TEXT NOT NULL,
    recipient          TEXT NOT NULL,
    installation_id    BIGINT REFERENCES installations(installation_id) ON DELETE SET NULL,
    resend_message_id  TEXT,
    sent_at            TIMESTAMPTZ NOT NULL DEFAULT now()
);
