---
name: projection-patterns
description: Build read models and projections from event streams. Use when implementing CQRS read sides, building materialized views, or optimizing query performance in event-sourced systems.
skill_id: architecture/projection-patterns
title: Projection Patterns
source_url: https://github.com/wshobson/agents/tree/0874a27b17a22e2b7c2e2bcd725768e0803cad06/plugins/backend-development/skills/projection-patterns
license: MIT
shelf: build
aletheore: {"id": "architecture/projection-patterns", "shelf": "build", "category": "architecture", "origin": "https://github.com/wshobson/agents/tree/0874a27b17a22e2b7c2e2bcd725768e0803cad06/plugins/backend-development/skills/projection-patterns", "license": "MIT", "license_source": "LICENSE", "upstream_commit": "0874a27b17a22e2b7c2e2bcd725768e0803cad06", "quality": 3, "curation_note": "CQRS/microservices/projection/saga", "needs_tool": null, "vet": {"injection_pattern_hits": [], "portability_flags": [], "companion_files": 1, "has_scripts": false}, "curated_on": "2026-10-18", "requires": [], "adapted": {}, "status": "ready", "mentions_agents": []}
---

# Projection Patterns

Comprehensive guide to building projections and read models for event-sourced systems.

## When to Use This Skill

- Building CQRS read models
- Creating materialized views from events
- Optimizing query performance
- Implementing real-time dashboards
- Building search indexes from events
- Aggregating data across streams

## Core Concepts

### 1. Projection Architecture

```
┌─────────────┐     ┌─────────────┐     ┌─────────────┐
│ Event Store │────►│ Projector   │────►│ Read Model  │
│             │     │             │     │ (Database)  │
│ ┌─────────┐ │     │ ┌─────────┐ │     │ ┌─────────┐ │
│ │ Events  │ │     │ │ Handler │ │     │ │ Tables  │ │
│ └─────────┘ │     │ │ Logic   │ │     │ │ Views   │ │
│             │     │ └─────────┘ │     │ │ Cache   │ │
└─────────────┘     └─────────────┘     └─────────────┘
```

### 2. Projection Types

| Type           | Description                 | Use Case               |
| -------------- | --------------------------- | ---------------------- |
| **Live**       | Real-time from subscription | Current state queries  |
| **Catchup**    | Process historical events   | Rebuilding read models |
| **Persistent** | Stores checkpoint           | Resume after restart   |
| **Inline**     | Same transaction as write   | Strong consistency     |

## Templates and detailed worked examples

Full template library and detailed worked examples live in `references/details.md`. Read that file when you need the concrete templates.

## Best Practices

### Do's

- **Make projections idempotent** - Safe to replay
- **Use transactions** - For multi-table updates
- **Store checkpoints** - Resume after failures
- **Monitor lag** - Alert on projection delays
- **Plan for rebuilds** - Design for reconstruction

### Don'ts

- **Don't couple projections** - Each is independent
- **Don't skip error handling** - Log and alert on failures
- **Don't ignore ordering** - Events must be processed in order
- **Don't over-normalize** - Denormalize for query patterns
