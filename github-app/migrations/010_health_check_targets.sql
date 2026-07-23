CREATE TABLE IF NOT EXISTS health_check_targets (
    id                    BIGSERIAL PRIMARY KEY,
    installation_id       BIGINT NOT NULL REFERENCES installations(installation_id) ON DELETE CASCADE,
    repo_full_name        TEXT NOT NULL,
    label                 TEXT NOT NULL,
    base_url              TEXT NOT NULL,
    latency_threshold_ms  INT,
    created_at            TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (installation_id, repo_full_name, base_url)
);

CREATE INDEX IF NOT EXISTS health_check_targets_lookup
ON health_check_targets (installation_id, repo_full_name);

ALTER TABLE endpoint_health ADD COLUMN IF NOT EXISTS target_id BIGINT REFERENCES health_check_targets(id) ON DELETE CASCADE;

CREATE INDEX IF NOT EXISTS endpoint_health_target_lookup ON endpoint_health (target_id);

-- Migrates each installation's single legacy base URL into a real target
-- row per repo it has scan history for, so an existing health-check
-- configuration keeps working under the multi-target model instead of
-- silently disappearing the moment this ships.
INSERT INTO health_check_targets (installation_id, repo_full_name, label, base_url, latency_threshold_ms)
SELECT DISTINCT i.installation_id, rh.repo_full_name, 'Primary', i.health_check_base_url, i.health_check_latency_threshold_ms
FROM installations i
JOIN repo_history rh ON rh.installation_id = i.installation_id
WHERE i.health_check_base_url IS NOT NULL
ON CONFLICT (installation_id, repo_full_name, base_url) DO NOTHING;
