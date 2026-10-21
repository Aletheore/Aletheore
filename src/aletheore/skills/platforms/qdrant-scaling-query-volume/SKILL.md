---
name: qdrant-scaling-query-volume
description: Guides Qdrant query volume scaling. Use when someone asks 'query returns too many results', 'scroll performance', 'large limit values', 'paginating search results', 'fetching many vectors', or 'high cardinality results'.
skill_id: platforms/qdrant-scaling-query-volume
title: Qdrant Scaling Query Volume
source_url: https://github.com/github/awesome-copilot/tree/cb64ad12e32737ae764becd6885462e9b4c7cb00/skills/qdrant-scaling/scaling-query-volume
license: MIT
shelf: build
aletheore: {"id": "platforms/qdrant-scaling-query-volume", "shelf": "build", "category": "platforms", "origin": "https://github.com/github/awesome-copilot/tree/cb64ad12e32737ae764becd6885462e9b4c7cb00/skills/qdrant-scaling/scaling-query-volume", "license": "MIT", "license_source": "LICENSE", "upstream_commit": "cb64ad12e32737ae764becd6885462e9b4c7cb00", "quality": 3, "curation_note": "Qdrant vendor skills", "needs_tool": null, "vet": {"injection_pattern_hits": [], "portability_flags": [], "companion_files": 0, "has_scripts": false}, "curated_on": "2026-10-18", "requires": [], "adapted": {}, "status": "ready", "mentions_agents": []}
---

# Scaling for Query Volume

Problem: When a query has a large limit (e.g. 1000) and there are multiple shards (e.g. 10), naively each shard must return the full 1000 results — totaling 10,000 scored points transferred and merged. This is wasteful since data is randomly distributed across auto-shards.

## Core idea

Instead of asking every shard for the full limit, ask each shard for a smaller limit computed via Poisson distribution statistics, then merge. This is safe because auto-sharding guarantees random, independent data distribution.

## When it activates

- More than 1 shard
- Auto-sharding is in use (all queried shards share the same shard key)
- The request's limit + offset >= SHARD_QUERY_SUBSAMPLING_LIMIT (128)
- The query is not exact

## Key tradeoff

 The strategy trades a small probability of slightly incomplete results for a large reduction in inter-shard data transfer, especially for high-limit queries across many shards. The 1.2x safety factor and the 99.9% Poisson threshold keep the error rate very low — comparable to inaccuracies already introduced by approximate vector indices like HNSW.