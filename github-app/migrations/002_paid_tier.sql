ALTER TABLE installations ADD COLUMN IF NOT EXISTS max_api_tokens INT NOT NULL DEFAULT 3;
ALTER TABLE installations ADD COLUMN IF NOT EXISTS webhook_url TEXT;

CREATE TABLE IF NOT EXISTS sessions (
    id                  TEXT PRIMARY KEY,
    github_user_id      BIGINT NOT NULL,
    github_login        TEXT NOT NULL,
    github_access_token TEXT NOT NULL,
    created_at          TIMESTAMPTZ NOT NULL DEFAULT now(),
    expires_at          TIMESTAMPTZ NOT NULL
);

CREATE TABLE IF NOT EXISTS api_tokens (
    id                      BIGSERIAL PRIMARY KEY,
    installation_id         BIGINT NOT NULL REFERENCES installations(installation_id) ON DELETE CASCADE,
    token_hash              TEXT NOT NULL UNIQUE,
    label                   TEXT NOT NULL,
    created_by_github_login TEXT NOT NULL,
    created_at              TIMESTAMPTZ NOT NULL DEFAULT now(),
    last_used_at            TIMESTAMPTZ,
    revoked_at              TIMESTAMPTZ
);

CREATE INDEX IF NOT EXISTS api_tokens_installation
ON api_tokens (installation_id)
WHERE revoked_at IS NULL;
