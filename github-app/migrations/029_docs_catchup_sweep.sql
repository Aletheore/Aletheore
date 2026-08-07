-- Tracks the last time the recurring Docs catch-up sweep ran for a repo -
-- deliberately separate from docs_build_status.updated_at, which also gets
-- touched by every push-triggered incremental update (far more often than
-- every 48h), so reusing it would make the sweep's own throttling check
-- almost never actually gate anything on an active repo.
CREATE TABLE IF NOT EXISTS docs_catchup_sweeps (
    installation_id  BIGINT NOT NULL REFERENCES installations(installation_id) ON DELETE CASCADE,
    repo_full_name   TEXT NOT NULL,
    last_swept_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (installation_id, repo_full_name)
);
