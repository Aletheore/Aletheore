-- Per-installation, per-repo dismissal of a specific secret or dependency-
-- vulnerability finding. Hosted-only - has no effect on a local `aletheore
-- scan`/`aletheore diff` and no write-back to the customer's own
-- .aletheore.json accepted_secrets baseline, which keeps working exactly as
-- it does today. identity_key is a canonical string computed server-side
-- from the finding's own fields (never trusted from a client request) - see
-- app_server/dismissed_findings.py's finding_identity_key().
CREATE TABLE IF NOT EXISTS dismissed_findings (
    id              BIGSERIAL PRIMARY KEY,
    installation_id BIGINT NOT NULL REFERENCES installations(installation_id) ON DELETE CASCADE,
    repo_full_name  TEXT NOT NULL,
    finding_type    TEXT NOT NULL CHECK (finding_type IN ('secret', 'vulnerability')),
    identity_key    TEXT NOT NULL,
    reason          TEXT,
    dismissed_by    TEXT NOT NULL,
    dismissed_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (installation_id, repo_full_name, finding_type, identity_key)
);

CREATE INDEX IF NOT EXISTS dismissed_findings_lookup
    ON dismissed_findings (installation_id, repo_full_name);
