-- F21: /admin/{org}/{repo}/public-status is a repo-scoped route (its
-- audit-log entry even records repo_full_name), but the flag it wrote was
-- installations.public_status_enabled - a single account-wide column. The
-- unauthenticated GET /v1/health/{org}/{repo} read side only checked that
-- column, never the requested repo, so opting in one public repo silently
-- exposed endpoint paths, reachability, and latency for every other repo
-- in the account, including private ones.
--
-- Fix: make the opt-in genuinely per-repo.
CREATE TABLE IF NOT EXISTS repo_public_status (
    installation_id BIGINT NOT NULL REFERENCES installations(installation_id) ON DELETE CASCADE,
    repo_full_name  TEXT NOT NULL,
    enabled         BOOLEAN NOT NULL DEFAULT false,
    updated_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (installation_id, repo_full_name)
);

-- Backfill from admin_action_log rather than defaulting every previously
-- account-wide-enabled installation to "on for all repos" (which would
-- just re-create the leak in migrated form) or "off for everyone" (which
-- would silently break a status page a customer already published). The
-- log already recorded which specific repo each toggle was for - take the
-- most recent action per (installation, repo) pair and keep only the ones
-- whose latest state was "enabled". An installation with no matching log
-- row (e.g. seeded outside the admin route) gets nothing backfilled and
-- stays off, consistent with "off by default" from migration 043.
INSERT INTO repo_public_status (installation_id, repo_full_name, enabled, updated_at)
SELECT installation_id, repo_full_name, enabled, updated_at
FROM (
    SELECT DISTINCT ON (installation_id, detail->>'repo_full_name')
        installation_id,
        detail->>'repo_full_name' AS repo_full_name,
        (detail->>'enabled')::boolean AS enabled,
        created_at AS updated_at
    FROM admin_action_log
    WHERE action = 'public_status_setting_changed'
      AND detail->>'repo_full_name' IS NOT NULL
    ORDER BY installation_id, detail->>'repo_full_name', created_at DESC
) latest
WHERE enabled = true
ON CONFLICT (installation_id, repo_full_name) DO NOTHING;

ALTER TABLE installations DROP COLUMN IF EXISTS public_status_enabled;
