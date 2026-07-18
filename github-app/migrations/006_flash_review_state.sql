CREATE TABLE IF NOT EXISTS flash_review_state (
    installation_id    BIGINT NOT NULL REFERENCES installations(installation_id) ON DELETE CASCADE,
    repo_full_name     TEXT NOT NULL,
    pr_number          INT NOT NULL,
    last_reviewed_sha  TEXT,
    last_attempted_at  TIMESTAMPTZ NOT NULL,
    PRIMARY KEY (installation_id, repo_full_name, pr_number)
);
