-- Tracks the last time the recurring AIRview catch-up sweep ran for a repo -
-- mirrors docs_catchup_sweeps exactly (029_docs_catchup_sweep.sql), kept as
-- its own table rather than shared: AIRview's full build and Docs' full
-- build are swept independently, on their own schedules, and a repo can be
-- due for one without being due for the other.
CREATE TABLE IF NOT EXISTS wiki_catchup_sweeps (
    installation_id  BIGINT NOT NULL REFERENCES installations(installation_id) ON DELETE CASCADE,
    repo_full_name   TEXT NOT NULL,
    last_swept_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (installation_id, repo_full_name)
);
