CREATE TABLE IF NOT EXISTS audit_reports (
    id                  BIGSERIAL PRIMARY KEY,
    installation_id     BIGINT NOT NULL REFERENCES installations(installation_id) ON DELETE CASCADE,
    repo_full_name      TEXT NOT NULL,
    verification_token  TEXT NOT NULL UNIQUE,
    report_text         TEXT NOT NULL,
    content_hash        TEXT NOT NULL,
    signature           TEXT NOT NULL,
    created_at          TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS audit_reports_token_lookup ON audit_reports (verification_token);
