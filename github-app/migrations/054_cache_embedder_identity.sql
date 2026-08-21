-- Batch 5 finding 8 (before_launch_fixes.md): neither cache table recorded
-- which embedder produced its stored vector, so a wrong-embedder cache hit
-- (e.g. mid-rollout during a future embedder switch) would pass the
-- array-length check in _cosine_similarity and get compared against a
-- different embedding space's query vectors - meaningless, but nothing
-- caught it except deploy-time discipline (remembering to write a purge
-- migration for the switch). NULL for every existing row (written before
-- this column existed, under an embedder identity nobody recorded) -
-- treated as a non-match at lookup time, same as a genuinely wrong
-- embedder, so historical rows age out naturally instead of serving a hit
-- nobody can verify.
ALTER TABLE evidence_packet_cache ADD COLUMN IF NOT EXISTS embedder TEXT;
ALTER TABLE flash_review_cache ADD COLUMN IF NOT EXISTS embedder TEXT;
