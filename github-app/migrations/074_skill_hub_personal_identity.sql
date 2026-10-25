CREATE TABLE IF NOT EXISTS personal_identities (
    id                  BIGSERIAL PRIMARY KEY,
    github_user_id      BIGINT NOT NULL UNIQUE,
    github_login        TEXT NOT NULL,
    created_at          TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS personal_api_tokens (
    id                      BIGSERIAL PRIMARY KEY,
    personal_identity_id    BIGINT NOT NULL REFERENCES personal_identities(id) ON DELETE CASCADE,
    token_hash              TEXT NOT NULL UNIQUE,
    label                   TEXT NOT NULL,
    created_at              TIMESTAMPTZ NOT NULL DEFAULT now(),
    last_used_at            TIMESTAMPTZ,
    revoked_at              TIMESTAMPTZ
);
CREATE INDEX IF NOT EXISTS personal_api_tokens_identity
ON personal_api_tokens (personal_identity_id)
WHERE revoked_at IS NULL;

CREATE TABLE IF NOT EXISTS custom_skills (
    id                      BIGSERIAL PRIMARY KEY,
    personal_identity_id    BIGINT NOT NULL REFERENCES personal_identities(id) ON DELETE CASCADE,
    skill_name              TEXT NOT NULL,
    content                 TEXT NOT NULL,
    created_at              TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at              TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (personal_identity_id, skill_name)
);
