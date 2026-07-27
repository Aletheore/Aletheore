-- Pro plan allows connecting unlimited repos, but only a limited number
-- of distinct repos may actually be scanned (any of: PR scan, Flash
-- review, managed audit) per calendar month - this table records which
-- repos already count toward that cap for a given installation/month.
CREATE TABLE IF NOT EXISTS monthly_scanned_repos (
    installation_id   BIGINT NOT NULL REFERENCES installations(installation_id) ON DELETE CASCADE,
    repo_full_name    TEXT NOT NULL,
    month             DATE NOT NULL,
    first_scanned_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (installation_id, repo_full_name, month)
);

CREATE INDEX IF NOT EXISTS monthly_scanned_repos_lookup ON monthly_scanned_repos (installation_id, month);
