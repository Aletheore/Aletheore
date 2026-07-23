-- Per-installation similarity cache for AIRview writing-stage model calls.
-- Rows are always queried by installation_id and repo_full_name before any
-- Python-side similarity comparison happens.
CREATE TABLE IF NOT EXISTS evidence_packet_cache (
    id               BIGSERIAL PRIMARY KEY,
    installation_id  BIGINT NOT NULL REFERENCES installations(installation_id) ON DELETE CASCADE,
    repo_full_name   TEXT NOT NULL,
    content_hash     TEXT NOT NULL,
    embedding        DOUBLE PRECISION[] NOT NULL,
    packet_json      JSONB NOT NULL,
    model_output     JSONB NOT NULL,
    model_used       TEXT NOT NULL,
    created_at       TIMESTAMPTZ NOT NULL DEFAULT now(),
    last_hit_at      TIMESTAMPTZ,
    hit_count        INT NOT NULL DEFAULT 0
);

CREATE INDEX IF NOT EXISTS evidence_packet_cache_lookup
ON evidence_packet_cache (installation_id, repo_full_name, created_at DESC);
