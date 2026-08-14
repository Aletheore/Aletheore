-- Switching the embedder from Ollama+nomic-embed-text to jina-embeddings-
-- v2-base-code (see scan_worker/embedding_client.py). Both happen to be
-- 768-dim, so old nomic vectors would pass the array-length check in
-- packet_cache.py's cosine similarity and get compared against new jina
-- query vectors - meaningless since they're different embedding spaces.
-- Downstream re-validation (see live_wiki.py's _validate_written_output)
-- would likely catch any resulting false match before it's served, but
-- there's no reason to spend cycles on lookups that are doomed by
-- construction. Both caches self-heal from here: a fresh row is written
-- on every miss.
TRUNCATE evidence_packet_cache;
TRUNCATE flash_review_cache;
