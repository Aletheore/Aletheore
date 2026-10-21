---
name: hybrid-search-implementation
description: Combine vector and keyword search for improved retrieval. Use when implementing RAG systems, building search engines, or when neither approach alone provides sufficient recall.
skill_id: ai-engineering/hybrid-search-implementation
title: Hybrid Search Implementation
source_url: https://github.com/wshobson/agents/tree/0874a27b17a22e2b7c2e2bcd725768e0803cad06/plugins/llm-application-dev/skills/hybrid-search-implementation
license: MIT
shelf: ai-agents
aletheore: {"id": "ai-engineering/hybrid-search-implementation", "shelf": "ai-agents", "category": "ai-engineering", "origin": "https://github.com/wshobson/agents/tree/0874a27b17a22e2b7c2e2bcd725768e0803cad06/plugins/llm-application-dev/skills/hybrid-search-implementation", "license": "MIT", "license_source": "LICENSE", "upstream_commit": "0874a27b17a22e2b7c2e2bcd725768e0803cad06", "quality": 3, "curation_note": "hybrid search and trace-to-training-data", "needs_tool": null, "vet": {"injection_pattern_hits": [], "portability_flags": [], "companion_files": 1, "has_scripts": false}, "curated_on": "2026-10-18", "requires": [], "adapted": {}, "status": "ready", "mentions_agents": []}
---

# Hybrid Search Implementation

Patterns for combining vector similarity and keyword-based search.

## When to Use This Skill

- Building RAG systems with improved recall
- Combining semantic understanding with exact matching
- Handling queries with specific terms (names, codes)
- Improving search for domain-specific vocabulary
- When pure vector search misses keyword matches

## Core Concepts

### 1. Hybrid Search Architecture

```
Query → ┬─► Vector Search ──► Candidates ─┐
        │                                  │
        └─► Keyword Search ─► Candidates ─┴─► Fusion ─► Results
```

### 2. Fusion Methods

| Method            | Description              | Best For        |
| ----------------- | ------------------------ | --------------- |
| **RRF**           | Reciprocal Rank Fusion   | General purpose |
| **Linear**        | Weighted sum of scores   | Tunable balance |
| **Cross-encoder** | Rerank with neural model | Highest quality |
| **Cascade**       | Filter then rerank       | Efficiency      |

## Templates and detailed worked examples

Full template library and detailed worked examples live in `references/details.md`. Read that file when you need the concrete templates.

## Best Practices

### Do's

- **Tune weights empirically** - Test on your data
- **Use RRF for simplicity** - Works well without tuning
- **Add reranking** - Significant quality improvement
- **Log both scores** - Helps with debugging
- **A/B test** - Measure real user impact

### Don'ts

- **Don't assume one size fits all** - Different queries need different weights
- **Don't skip keyword search** - Handles exact matches better
- **Don't over-fetch** - Balance recall vs latency
- **Don't ignore edge cases** - Empty results, single word queries
