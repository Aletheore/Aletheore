-- Maps a Flash Review finding's identity (see app_server/dismissed_findings.py's
-- finding_identity_key for flash_review_llm/flash_review_semantic) to the
-- real GitHub inline review-comment id that finding was posted as, per PR.
--
-- Scoped by pr_number (unlike dismissed_findings, which is repo-wide) - a
-- finding's dismissal should follow the underlying bug across every PR on
-- the repo, but a comment is inherently one PR's artifact; the same
-- identity_key can have a different github_comment_id on two different
-- PRs, and needs to.
--
-- Needed because posting moved from one upserted issue-comment
-- (jobs.py's FLASH_REVIEW_MARKER pattern - a single comment, trivially
-- reconciled by re-fetching and matching a marker string) to one inline
-- review comment per finding: a re-review has to know which of N existing
-- comments corresponds to which of this push's findings, so a still-
-- present finding gets left untouched (not reposted/duplicated) and only
-- genuinely new findings get a new comment.
--
-- resolved_at is set (once) the first time a re-review's finding list no
-- longer includes this identity_key - the comment itself is left in place
-- and edited to say so (see run_flash_review_job), never deleted: a human
-- may have already replied to it, and deleting a comment with real replies
-- attached destroys that thread. NULL until then; once set, a further
-- re-review that also doesn't detect the finding does not re-edit the
-- comment again (checked before editing, not just before this row's
-- write) - the edit itself is a one-time transition, not resynced on every
-- subsequent silent push.
CREATE TABLE IF NOT EXISTS flash_review_finding_comments (
    id                BIGSERIAL PRIMARY KEY,
    installation_id   BIGINT NOT NULL REFERENCES installations(installation_id) ON DELETE CASCADE,
    repo_full_name    TEXT NOT NULL,
    pr_number         INT NOT NULL,
    finding_type      TEXT NOT NULL CHECK (finding_type IN ('flash_review_llm', 'flash_review_semantic')),
    identity_key      TEXT NOT NULL,
    github_comment_id BIGINT NOT NULL,
    last_seen_sha     TEXT NOT NULL,
    resolved_at       TIMESTAMPTZ,
    created_at        TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (installation_id, repo_full_name, pr_number, finding_type, identity_key)
);

CREATE INDEX IF NOT EXISTS flash_review_finding_comments_lookup
    ON flash_review_finding_comments (installation_id, repo_full_name, pr_number);

-- The reply-webhook path (pull_request_review_comment, in_reply_to_id set)
-- only ever has the real GitHub comment id to start from, not the
-- installation/repo/pr/identity_key tuple - this is the lookup that path
-- needs.
CREATE INDEX IF NOT EXISTS flash_review_finding_comments_by_github_id
    ON flash_review_finding_comments (github_comment_id);
