-- Audit trail for customer data deletion.
--
-- installation_id is deliberately a bare BIGINT with no REFERENCES
-- installations(installation_id): every other installation-scoped table
-- cascades on delete, and a cascading FK here would destroy the record of
-- the deletion as part of the very deletion it exists to document.
--
-- This table is therefore never truncated by an installation purge, and is
-- the one place where an account_login outlives the account. That is the
-- point - "we deleted everything and kept no proof" is not an audit trail.
CREATE TABLE IF NOT EXISTS data_deletion_log (
    id               BIGSERIAL PRIMARY KEY,
    installation_id  BIGINT NOT NULL,
    account_login    TEXT NOT NULL,
    actor_login      TEXT NOT NULL,
    repos_deleted    INT NOT NULL DEFAULT 0,
    users_purged     INT NOT NULL DEFAULT 0,
    deleted_at       TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS data_deletion_log_installation_idx
    ON data_deletion_log (installation_id, deleted_at DESC);
