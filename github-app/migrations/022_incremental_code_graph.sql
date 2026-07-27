-- Durable, incrementally-updated code graph: the persistent counterpart to
-- repo_history's evidence JSONB blob. repo_history stores one full,
-- whole-repo snapshot per scan - useful as a point-in-time record, but it
-- gives no way to update only what a push actually changed, and no way to
-- query a single file/symbol/dependency edge/endpoint without pulling and
-- re-parsing the entire latest blob. These tables let a push update only
-- the rows for files that actually changed, matching the same
-- (installation_id, repo_full_name, branch) keying convention already
-- established by evidence_git_* (see 020_evidence_git_graph.sql) for the
-- git-history/ownership graph.
--
-- Scope note: this models file/symbol/dependency-edge/endpoint state only.
-- Ownership is already covered by evidence_git_ownership; findings
-- (secrets/audit/Flash Review results, with an open/resolved lifecycle)
-- are deliberately out of scope here and left for a follow-up once this
-- graph foundation is proven in production.

CREATE TABLE IF NOT EXISTS code_graph_sync_state (
    installation_id  BIGINT NOT NULL REFERENCES installations(installation_id) ON DELETE CASCADE,
    repo_full_name   TEXT NOT NULL,
    branch           TEXT NOT NULL,
    last_synced_sha  TEXT NOT NULL,
    last_synced_at   TIMESTAMPTZ NOT NULL,
    PRIMARY KEY (installation_id, repo_full_name, branch)
);

CREATE TABLE IF NOT EXISTS code_graph_files (
    installation_id  BIGINT NOT NULL REFERENCES installations(installation_id) ON DELETE CASCADE,
    repo_full_name   TEXT NOT NULL,
    branch           TEXT NOT NULL,
    path             TEXT NOT NULL,
    language         TEXT,
    content_hash     TEXT NOT NULL,
    updated_at       TIMESTAMPTZ NOT NULL,
    PRIMARY KEY (installation_id, repo_full_name, branch, path)
);

CREATE TABLE IF NOT EXISTS code_graph_symbols (
    installation_id  BIGINT NOT NULL REFERENCES installations(installation_id) ON DELETE CASCADE,
    repo_full_name   TEXT NOT NULL,
    branch           TEXT NOT NULL,
    path             TEXT NOT NULL,
    name             TEXT NOT NULL,
    kind             TEXT NOT NULL,
    start_line       INT NOT NULL,
    end_line         INT NOT NULL,
    PRIMARY KEY (installation_id, repo_full_name, branch, path, name, start_line)
);

-- Every incremental update replaces "all symbols for this file" as a unit
-- (see code_graph_store.py's apply_file_deltas) - this is the lookup this
-- pattern needs, separate from the primary key above.
CREATE INDEX IF NOT EXISTS code_graph_symbols_by_path
ON code_graph_symbols (installation_id, repo_full_name, branch, path);

CREATE TABLE IF NOT EXISTS code_graph_dependency_edges (
    installation_id  BIGINT NOT NULL REFERENCES installations(installation_id) ON DELETE CASCADE,
    repo_full_name   TEXT NOT NULL,
    branch           TEXT NOT NULL,
    from_path        TEXT NOT NULL,
    to_path          TEXT NOT NULL,
    PRIMARY KEY (installation_id, repo_full_name, branch, from_path, to_path)
);

-- "What depends on this file" (used to find affected dependent graph
-- regions when a file changes) queries by to_path - the primary key above
-- is ordered by from_path first, so this needs its own index.
CREATE INDEX IF NOT EXISTS code_graph_edges_by_to_path
ON code_graph_dependency_edges (installation_id, repo_full_name, branch, to_path);

CREATE TABLE IF NOT EXISTS code_graph_endpoints (
    installation_id  BIGINT NOT NULL REFERENCES installations(installation_id) ON DELETE CASCADE,
    repo_full_name   TEXT NOT NULL,
    branch           TEXT NOT NULL,
    method           TEXT NOT NULL,
    endpoint_path    TEXT NOT NULL,
    file_path        TEXT NOT NULL,
    line             INT,
    PRIMARY KEY (installation_id, repo_full_name, branch, method, endpoint_path)
);

CREATE INDEX IF NOT EXISTS code_graph_endpoints_by_file
ON code_graph_endpoints (installation_id, repo_full_name, branch, file_path);
