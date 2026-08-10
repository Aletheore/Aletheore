-- Durable, plan-independent record of every github_login that has ever
-- passed _require_authorized_installation for a given installation.
--
-- installation_members alone is not enough for this: it's only ever
-- populated for paid-plan seat holders (_require_seat_if_paid skips free
-- plans entirely, by design - there's no seat revenue to protect there).
-- github_user_emails and sessions are captured on login independent of
-- plan or seat status, so a free-plan installation's real users have real
-- PII with nothing in installation_members to find them by. This table is
-- purge_installation_data's actual source of truth for "who might have PII
-- tied to this installation" - installation_members remains exactly what
-- it was, seat/billing bookkeeping, untouched by this table's existence.
--
-- ON DELETE CASCADE mirrors installation_members: rows for an installation
-- are read here (for the purge's membership list) before the cascade
-- removes them, same ordering purge_installation_data already relies on.
CREATE TABLE IF NOT EXISTS installation_access_log (
    installation_id  BIGINT NOT NULL REFERENCES installations(installation_id) ON DELETE CASCADE,
    github_login     TEXT NOT NULL,
    first_seen_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
    last_seen_at     TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (installation_id, github_login)
);

-- Supports "does this login still have access to any *other* installation"
-- during a purge - a lookup by github_login alone, across all installations.
CREATE INDEX IF NOT EXISTS installation_access_log_login_idx
    ON installation_access_log (github_login);
