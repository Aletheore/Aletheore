-- Tracks the last time the weekly usage digest was sent per installation.
-- Separate from docs_catchup_sweeps: that table is per-repo and gated on
-- real activity since the last sweep (no point re-describing an unchanged
-- repo); this is per-installation and unconditional - the digest is
-- explicitly meant to re-engage installs that have gone quiet, so a week
-- with nothing to report still gets an email, not silence.
CREATE TABLE IF NOT EXISTS digest_sends (
    installation_id  BIGINT PRIMARY KEY REFERENCES installations(installation_id) ON DELETE CASCADE,
    last_sent_at     TIMESTAMPTZ NOT NULL DEFAULT now()
);
