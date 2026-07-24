CREATE TABLE IF NOT EXISTS mcp_git_mirrors (
    id BIGSERIAL PRIMARY KEY,
    installation_id BIGINT NOT NULL REFERENCES installations(installation_id) ON DELETE CASCADE,
    repo_full_name TEXT NOT NULL,
    local_path TEXT NOT NULL,
    last_synced_commit TEXT,
    last_synced_at TIMESTAMPTZ,
    size_bytes BIGINT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (installation_id, repo_full_name)
);
