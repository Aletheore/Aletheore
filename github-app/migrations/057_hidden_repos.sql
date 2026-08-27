-- Reversible "soft removal" state for a repo whose access was revoked via
-- an installation_repositories/removed webhook - deselected from the
-- GitHub App's repo list, distinct from uninstalling the whole app (see
-- webhooks/installation.py, and purge_installation_data for the
-- irreversible full-uninstall purge this is deliberately NOT). A row's
-- existence means: hide the repo from the dashboard's repo list, and stop
-- enqueuing any new scan/review work for it. Re-adding the repo
-- (installation_repositories/added) deletes the row - reversible, no data
-- loss, since the customer only deselected one repo, not disconnected the
-- app.
CREATE TABLE IF NOT EXISTS hidden_repos (
    installation_id BIGINT NOT NULL REFERENCES installations(installation_id) ON DELETE CASCADE,
    repo_full_name  TEXT NOT NULL,
    hidden_at       TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (installation_id, repo_full_name)
);
