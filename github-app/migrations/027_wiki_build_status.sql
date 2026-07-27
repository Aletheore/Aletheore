CREATE TABLE IF NOT EXISTS wiki_build_status (
    installation_id  BIGINT NOT NULL REFERENCES installations(installation_id) ON DELETE CASCADE,
    repo_full_name   TEXT NOT NULL,
    status           TEXT NOT NULL,
    error_message    TEXT,
    updated_at       TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (installation_id, repo_full_name)
);
