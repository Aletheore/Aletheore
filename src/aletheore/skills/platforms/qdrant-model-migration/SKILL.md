---
name: qdrant-model-migration
description: Guides embedding model migration in Qdrant without downtime. Use when someone asks 'how to switch embedding models', 'how to migrate vectors', 'how to update to a new model', 'zero-downtime model change', 'how to re-embed my data', or 'can I use two models at once'. Also use when upgrading model dimensions, switching providers, or A/B testing models.
skill_id: platforms/qdrant-model-migration
title: Qdrant Model Migration
source_url: https://github.com/github/awesome-copilot/tree/cb64ad12e32737ae764becd6885462e9b4c7cb00/skills/qdrant-model-migration
license: MIT
shelf: build
aletheore: {"id": "platforms/qdrant-model-migration", "shelf": "build", "category": "platforms", "origin": "https://github.com/github/awesome-copilot/tree/cb64ad12e32737ae764becd6885462e9b4c7cb00/skills/qdrant-model-migration", "license": "MIT", "license_source": "LICENSE", "upstream_commit": "cb64ad12e32737ae764becd6885462e9b4c7cb00", "quality": 3, "curation_note": "Qdrant vendor skills", "needs_tool": null, "vet": {"injection_pattern_hits": [], "portability_flags": [], "companion_files": 0, "has_scripts": false}, "curated_on": "2026-10-18", "requires": [], "adapted": {}, "status": "ready", "mentions_agents": []}
---

# What to Do When Changing Embedding Models

Vectors from different models are incompatible. You cannot mix old and new embeddings in the same vector space. You also cannot add new named vector fields to an existing collection. All named vectors must be defined at collection creation time. Both migration strategies below require creating a new collection.

- Understand collection aliases before choosing a strategy [Collection aliases](https://search.qdrant.tech/md/documentation/manage-data/collections/?s=collection-aliases)


## Can I Avoid Re-embedding?

Use when: looking for shortcuts before committing to full migration.

You MUST re-embed if: changing model provider (OpenAI to Cohere), changing architecture (CLIP to BGE), incompatible dimension counts across different models, or adding sparse vectors to dense-only collection.

You CAN avoid re-embedding if: using Matryoshka models (use `dimensions` parameter to output lower-dimensional embeddings, learn linear transformation from sample data, some recall loss, good for 100M+ datasets). Or changing quantization (binary to scalar): Qdrant re-quantizes automatically. [Quantization](https://search.qdrant.tech/md/documentation/manage-data/quantization/)


## Need Zero Downtime (Alias Swap)

Use when: production must stay available. Recommended for model replacement at scale.

- Create a new collection with the new model's dimensions and distance metric
- Re-embed all data into the new collection in the background
- Point your application at a collection alias instead of a direct collection name
- Atomically swap the alias to the new collection [Switch collection](https://search.qdrant.tech/md/documentation/manage-data/collections/?s=switch-collection)
- Verify search quality, then delete the old collection

Careful, the alias swap only redirects queries. Payloads must be re-uploaded separately.


## Need Both Models Live (Side-by-Side)

Use when: A/B testing models, multi-modal (dense + sparse), or evaluating a new model before committing.

You cannot add a named vector to an existing collection. Create a new collection with both vector fields defined upfront:

- Create new collection with old and new named vectors both defined [Collection with multiple vectors](https://search.qdrant.tech/md/documentation/manage-data/collections/?s=collection-with-multiple-vectors)
- Migrate data from old collection, preserving existing vectors in the old named field
- Backfill new model embeddings incrementally using `UpdateVectors` [Update vectors](https://search.qdrant.tech/md/documentation/manage-data/points/?s=update-vectors)
- Compare quality by querying with `using: "old_model"` vs `using: "new_model"`
- Swap alias to new collection once satisfied

Co-locating large multi-vectors (especially ColBERT) with dense vectors degrades ALL queries, even those only using dense. At millions of points, users report 13s latency dropping to 2s after removing ColBERT. Put large vectors on disk during side-by-side migration.

If you anticipate future model migrations, define both vector fields upfront at collection creation.


## Dense to Hybrid Search Migration

Use when: adding sparse/BM25 vectors to an existing dense-only collection. Most common migration pattern.

You cannot add sparse vectors to an existing dense-only collection. Must recreate:

- Create new collection with both dense and sparse vector configs defined
- Re-embed all data with both dense and sparse models
- Migrate payloads, swap alias

Sparse vectors at chunk level have different TF-IDF characteristics than document level. Test retrieval quality after migration, especially for non-English text without stop-word removal.


## Re-embedding Is Too Slow

Use when: dataset is large and re-embedding is the bottleneck.

- Use `update_mode: insert` (v1.17+) for safe idempotent migration [Update mode](https://search.qdrant.tech/md/documentation/manage-data/points/?s=update-mode)
- Scroll the old collection with `with_vectors=False`, re-embed in batches, upsert into new collection
- Upload in parallel batches (64-256 points per request, 2-4 parallel streams) [Bulk upload](https://search.qdrant.tech/md/documentation/tutorials-develop/bulk-upload/)
- Disable HNSW during bulk load (set `indexing_threshold_kb` very high, restore after)
- For Qdrant Cloud inference, switching models is a config change, not a pipeline change [Inference docs](https://search.qdrant.tech/md/documentation/inference/)

For 400GB+ datasets, expect days. For small datasets (<25MB), re-indexing from source is faster than using the migration tool.


## What NOT to Do

- Assume you can add named vectors to an existing collection (must be defined at creation time)
- Delete the old collection before verifying the new one
- Forget to update the query embedding model in your application code
- Skip payload migration when using alias swap (aliases redirect queries, they do not copy data)
- Keep ColBERT vectors co-located with dense vectors during a long migration (I/O cost degrades all queries)
- Migrate to hybrid search without testing BM25 quality at chunk level
