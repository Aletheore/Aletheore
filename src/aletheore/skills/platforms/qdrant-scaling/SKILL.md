---
name: qdrant-scaling
description: Guides Qdrant scaling decisions. Use when someone asks 'how many nodes do I need', 'data doesn't fit on one node', 'need more throughput', 'cluster is slow', 'too many tenants', 'vertical or horizontal', 'how to shard', or 'need to add capacity'.
allowed-tools:
- Read
- Grep
- Glob
skill_id: platforms/qdrant-scaling
title: Qdrant Scaling
source_url: https://github.com/github/awesome-copilot/tree/cb64ad12e32737ae764becd6885462e9b4c7cb00/skills/qdrant-scaling
license: MIT
shelf: build
aletheore: {"id": "platforms/qdrant-scaling", "shelf": "build", "category": "platforms", "origin": "https://github.com/github/awesome-copilot/tree/cb64ad12e32737ae764becd6885462e9b4c7cb00/skills/qdrant-scaling", "license": "MIT", "license_source": "LICENSE", "upstream_commit": "cb64ad12e32737ae764becd6885462e9b4c7cb00", "quality": 3, "curation_note": "Qdrant vendor skills", "needs_tool": null, "vet": {"injection_pattern_hits": [], "portability_flags": [], "companion_files": 8, "has_scripts": false}, "curated_on": "2026-10-18", "requires": [], "adapted": {}, "status": "ready", "mentions_agents": []}
---

# Qdrant Scaling

First determine what you're scaling for:

- data volume
- query throughput (QPS)
- query latency
- query volume

After determining the scaling goal, we can choose scaling strategy based on tradeoffs and assumptions.
Each pulls toward different strategies. Scaling for throughput and latency are opposite tuning directions.


## Scaling Data Volume

This becomes relevant when volume of the dataset exceeds the capacity of a single node.
Read more about scaling for data volume in [Scaling Data Volume](scaling-data-volume/SKILL.md)


## Scaling for Query Throughput

If your system needs to handle more parallel queries than a single node can handle,
 then you need to scale for query throughput.

Read more about scaling for query throughput in [Scaling for Query Throughput](scaling-qps/SKILL.md)

## Scaling for Query Latency

Latency of a single query is determined by the slowest component in the query execution path.
It is in sometimes correlated with throughput, but not always. It might require different strategies for scaling.

Read more about scaling for query latency in [Scaling for Query Latency](minimize-latency/SKILL.md)


## Scaling for Query Volume

By query volume we understand the amount of results that a single query returns. 
If the query volume is too high, it can cause performance issues and increase latency.

Tuning for query volume is opposite might require special strategies. 

Read more about scaling for query volume in [Scaling for Query Volume](scaling-query-volume/SKILL.md)
