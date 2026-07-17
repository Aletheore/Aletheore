CREATE TABLE installations (
    installation_id BIGINT PRIMARY KEY,
    account_login   TEXT NOT NULL,
    plan            TEXT NOT NULL DEFAULT 'free',
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at      TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE repo_history (
    id              BIGSERIAL PRIMARY KEY,
    installation_id BIGINT NOT NULL REFERENCES installations(installation_id) ON DELETE CASCADE,
    repo_full_name  TEXT NOT NULL,
    scanned_at      TIMESTAMPTZ NOT NULL,
    evidence        JSONB NOT NULL,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX repo_history_lookup ON repo_history (installation_id, repo_full_name, scanned_at DESC);
