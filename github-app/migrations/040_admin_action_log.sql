-- Audit trail for admin-mutating dashboard actions other than deletion
-- (which already has its own data_deletion_log - see 035).
--
-- Unlike data_deletion_log, this cascades with the installation: there is
-- no "prove we destroyed everything" requirement here, the installation
-- still exists, and its own action history going with it on a real delete
-- is the expected, correct behavior, not a gap to design around.
CREATE TABLE IF NOT EXISTS admin_action_log (
    id               BIGSERIAL PRIMARY KEY,
    installation_id  BIGINT NOT NULL REFERENCES installations(installation_id) ON DELETE CASCADE,
    actor_login      TEXT NOT NULL,
    action           TEXT NOT NULL,
    detail           JSONB,
    created_at       TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS admin_action_log_installation_idx
    ON admin_action_log (installation_id, created_at DESC);
