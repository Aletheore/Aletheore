CREATE TABLE IF NOT EXISTS mcp_code_embeddings (
    id BIGSERIAL PRIMARY KEY,
    installation_id BIGINT NOT NULL REFERENCES installations(installation_id) ON DELETE CASCADE,
    repo_full_name TEXT NOT NULL,
    file_path TEXT NOT NULL,
    chunk_index INT NOT NULL,
    content_hash TEXT NOT NULL,
    chunk_text TEXT NOT NULL,
    embedding DOUBLE PRECISION[] NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (installation_id, repo_full_name, file_path, chunk_index)
);

CREATE INDEX IF NOT EXISTS mcp_code_embeddings_lookup
ON mcp_code_embeddings (installation_id, repo_full_name, file_path);
