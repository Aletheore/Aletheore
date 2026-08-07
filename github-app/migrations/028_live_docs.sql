CREATE TABLE IF NOT EXISTS docs_symbols (
    installation_id  BIGINT NOT NULL REFERENCES installations(installation_id) ON DELETE CASCADE,
    repo_full_name   TEXT NOT NULL,
    module_path      TEXT NOT NULL,
    symbol_name      TEXT NOT NULL,
    description      TEXT NOT NULL,
    mode             TEXT NOT NULL,
    source_commit    TEXT,
    updated_at       TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (installation_id, repo_full_name, module_path, symbol_name)
);

CREATE INDEX IF NOT EXISTS docs_symbols_lookup
ON docs_symbols (installation_id, repo_full_name);

CREATE TABLE IF NOT EXISTS docs_build_status (
    installation_id  BIGINT NOT NULL REFERENCES installations(installation_id) ON DELETE CASCADE,
    repo_full_name   TEXT NOT NULL,
    status           TEXT NOT NULL,
    error_message    TEXT,
    updated_at       TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (installation_id, repo_full_name)
);
