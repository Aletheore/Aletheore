-- Per-PR outcome log for Flash Review, for the "review history" list on the
-- Flash credits page. Confirmed a real gap before adding this: none of the
-- existing Flash Review tables record it -
-- flash_review_state (006) is latest-state only (one row per PR, overwritten
-- every run), flash_review_cache (013) is a similarity/embedding cache keyed
-- by content_hash with no pr_number column, flash_review_finding_comments
-- (059) only gets a row when a finding produced a real posted GitHub
-- comment (a clean review or a skip leaves zero rows), and
-- llm_spend_events (062) has no repo_full_name or pr_number. A customer
-- asking "did Flash Review even run on my last PR" currently has no answer
-- anywhere in the schema.
--
-- outcome is the four shapes a Flash Review run actually ends in: it
-- posted at least one finding, it ran clean (nothing held up - a genuinely
-- clean diff, everything already dismissed, or rejected by grounding/
-- verification), it was skipped before running (free-tier exhausted,
-- spend-reservation exhausted, etc), or it failed (an unhandled exception,
-- or real findings held up but every post attempt to GitHub failed - the
-- latter is deliberately not "clean", which would wrongly read as "we
-- checked, no issues" when the truth is "we found something and couldn't
-- tell you"). skip_reason doubles as a free-text detail for both skipped
-- and failed rows (never the raw exception text for a failure - this table
-- is read back on a customer-facing page). finding_count is 0 except on a
-- posted row.
CREATE TABLE IF NOT EXISTS flash_review_history (
    id              BIGSERIAL PRIMARY KEY,
    installation_id BIGINT NOT NULL REFERENCES installations(installation_id) ON DELETE CASCADE,
    repo_full_name  TEXT NOT NULL,
    pr_number       INT NOT NULL,
    outcome         TEXT NOT NULL CHECK (outcome IN ('posted', 'clean', 'skipped', 'failed')),
    finding_count   INT NOT NULL DEFAULT 0,
    skip_reason     TEXT,
    reviewed_at     TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- The review-history list reads "most recent N runs for this installation
-- across all its repos" - not scoped to one repo, since a Flash org can
-- have several.
CREATE INDEX IF NOT EXISTS flash_review_history_by_installation
    ON flash_review_history (installation_id, reviewed_at DESC);
