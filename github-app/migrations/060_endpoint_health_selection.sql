-- Lets a customer explicitly choose which endpoints get health-checked
-- when a repo has more real API endpoints than
-- jobs.MAX_HEALTH_CHECK_ENDPOINTS_PER_TARGET (64). Presence of a row is
-- the whole signal - there is no boolean column, because "not selected"
-- and "not monitored" are the same fact: once a customer has made ANY
-- explicit selection for a repo, exactly the endpoints with a row here
-- are monitored (see jobs._candidate_endpoints), and a repo with zero
-- rows falls back to today's default (the first N endpoints in scan
-- order), unchanged. Emptying the selection entirely (deleting every
-- row) is how a customer resets a repo back to that default.
CREATE TABLE IF NOT EXISTS endpoint_health_selection (
    id                BIGSERIAL PRIMARY KEY,
    installation_id   BIGINT NOT NULL REFERENCES installations(installation_id) ON DELETE CASCADE,
    repo_full_name    TEXT NOT NULL,
    endpoint_method   TEXT NOT NULL,
    endpoint_path     TEXT NOT NULL,
    created_at        TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (installation_id, repo_full_name, endpoint_method, endpoint_path)
);

CREATE INDEX IF NOT EXISTS endpoint_health_selection_lookup
ON endpoint_health_selection (installation_id, repo_full_name);
