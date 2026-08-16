-- Persistent, incremental git-history graph for the hosted service - the
-- Postgres counterpart to the CLI's local .aletheore/graph.db (see
-- prototype/aletheore/git_intel/sqlite_store.py). Every hosted scan clones
-- a fresh, throwaway copy of the repo, so without this the CLI's own
-- local incremental cache never persists between scans - every scan would
-- still be a from-scratch baseline walk. This is what makes repeat scans
-- of the same installation's repo actually incremental, and gives
-- runtime-failure correlation a persistent index to query instead of a
-- live GitHub API round-trip per lookup.
CREATE TABLE IF NOT EXISTS evidence_git_sync_state (
    installation_id  BIGINT NOT NULL REFERENCES installations(installation_id) ON DELETE CASCADE,
    repo_full_name   TEXT NOT NULL,
    branch           TEXT NOT NULL,
    last_synced_sha  TEXT NOT NULL,
    last_synced_at   TIMESTAMPTZ NOT NULL,
    PRIMARY KEY (installation_id, repo_full_name, branch)
);

CREATE TABLE IF NOT EXISTS evidence_git_ownership (
    installation_id  BIGINT NOT NULL REFERENCES installations(installation_id) ON DELETE CASCADE,
    repo_full_name   TEXT NOT NULL,
    branch           TEXT NOT NULL,
    email            TEXT NOT NULL,
    names            JSONB NOT NULL,
    commit_count     INT NOT NULL,
    PRIMARY KEY (installation_id, repo_full_name, branch, email)
);

CREATE TABLE IF NOT EXISTS evidence_git_cadence (
    installation_id  BIGINT NOT NULL REFERENCES installations(installation_id) ON DELETE CASCADE,
    repo_full_name   TEXT NOT NULL,
    branch           TEXT NOT NULL,
    week_start       DATE NOT NULL,
    commit_count     INT NOT NULL,
    PRIMARY KEY (installation_id, repo_full_name, branch, week_start)
);

CREATE TABLE IF NOT EXISTS evidence_git_file_churn (
    installation_id    BIGINT NOT NULL REFERENCES installations(installation_id) ON DELETE CASCADE,
    repo_full_name     TEXT NOT NULL,
    branch             TEXT NOT NULL,
    path               TEXT NOT NULL,
    churn_count        INT NOT NULL,
    recent_commits     JSONB NOT NULL,
    co_change_counts   JSONB NOT NULL,
    owners             JSONB NOT NULL DEFAULT '{}'::jsonb,
    PRIMARY KEY (installation_id, repo_full_name, branch, path)
);

-- Runtime correlation (failing endpoint -> recent commits/owner for its
-- file) always looks up by path within one repo+branch - this is the
-- lookup index, separate from the primary key above which is used for
-- the full-graph read/write on each scan.
CREATE INDEX IF NOT EXISTS evidence_git_file_churn_lookup
ON evidence_git_file_churn (installation_id, repo_full_name, branch, path);
