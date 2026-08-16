-- Per-file ownership breakdown for file-scoped ownership queries.
ALTER TABLE evidence_git_file_churn
ADD COLUMN IF NOT EXISTS owners JSONB NOT NULL DEFAULT '{}'::jsonb;
