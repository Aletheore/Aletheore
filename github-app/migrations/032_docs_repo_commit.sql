-- Opt-in per repo: whether the grounded API reference (docs_reference.py's
-- build_combined_reference) also gets pushed into the customer's own repo
-- as .aletheore/docs/API.md, via a single rolling PR (aletheore/docs-update)
-- that gets updated in place rather than a fresh PR every run. Off by
-- default - this is the one Docs surface that writes to a customer's repo
-- rather than just reading from it, so it needs an explicit opt-in per repo
-- rather than being bundled into the existing Docs paid-plan gate.
--
-- last_content_hash lets the commit job skip a run when nothing changed
-- since the last push (no point opening/updating a PR with an empty diff).
-- pr_number is the last-known PR from that branch - re-checked for "still
-- open" before reuse, since a customer may have merged or closed it.
CREATE TABLE IF NOT EXISTS docs_repo_commit_settings (
    installation_id    BIGINT NOT NULL REFERENCES installations(installation_id) ON DELETE CASCADE,
    repo_full_name     TEXT NOT NULL,
    enabled            BOOLEAN NOT NULL DEFAULT false,
    last_content_hash  TEXT,
    pr_number          INTEGER,
    updated_at         TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (installation_id, repo_full_name)
);
