---
name: event-store-design
description: Design and implement event stores for event-sourced systems. Use when building event sourcing infrastructure, choosing event store technologies, or implementing event persistence patterns.
skill_id: architecture/event-store-design
title: Event Store Design
source_url: https://github.com/wshobson/agents/tree/0874a27b17a22e2b7c2e2bcd725768e0803cad06/plugins/backend-development/skills/event-store-design
license: MIT
shelf: build
aletheore: {"id": "architecture/event-store-design", "shelf": "build", "category": "architecture", "origin": "https://github.com/wshobson/agents/tree/0874a27b17a22e2b7c2e2bcd725768e0803cad06/plugins/backend-development/skills/event-store-design", "license": "MIT", "license_source": "LICENSE", "upstream_commit": "0874a27b17a22e2b7c2e2bcd725768e0803cad06", "quality": 3, "curation_note": "event store design", "needs_tool": null, "vet": {"injection_pattern_hits": [], "portability_flags": [], "companion_files": 1, "has_scripts": false}, "curated_on": "2026-10-18", "requires": [], "adapted": {}, "status": "ready", "mentions_agents": []}
---

# Event Store Design

Comprehensive guide to designing event stores for event-sourced applications.

## When to Use This Skill

- Designing event sourcing infrastructure
- Choosing between event store technologies
- Implementing custom event stores
- Optimizing event storage and retrieval
- Setting up event store schemas
- Planning for event store scaling

## Core Concepts

### 1. Event Store Architecture

```
┌─────────────────────────────────────────────────────┐
│                    Event Store                       │
├─────────────────────────────────────────────────────┤
│  ┌─────────────┐  ┌─────────────┐  ┌─────────────┐ │
│  │   Stream 1   │  │   Stream 2   │  │   Stream 3   │ │
│  │ (Aggregate)  │  │ (Aggregate)  │  │ (Aggregate)  │ │
│  ├─────────────┤  ├─────────────┤  ├─────────────┤ │
│  │ Event 1     │  │ Event 1     │  │ Event 1     │ │
│  │ Event 2     │  │ Event 2     │  │ Event 2     │ │
│  │ Event 3     │  │ ...         │  │ Event 3     │ │
│  │ ...         │  │             │  │ Event 4     │ │
│  └─────────────┘  └─────────────┘  └─────────────┘ │
├─────────────────────────────────────────────────────┤
│  Global Position: 1 → 2 → 3 → 4 → 5 → 6 → ...     │
└─────────────────────────────────────────────────────┘
```

### 2. Event Store Requirements

| Requirement       | Description                        |
| ----------------- | ---------------------------------- |
| **Append-only**   | Events are immutable, only appends |
| **Ordered**       | Per-stream and global ordering     |
| **Versioned**     | Optimistic concurrency control     |
| **Subscriptions** | Real-time event notifications      |
| **Idempotent**    | Handle duplicate writes safely     |

## Technology Comparison

| Technology       | Best For                  | Limitations                      |
| ---------------- | ------------------------- | -------------------------------- |
| **EventStoreDB** | Pure event sourcing       | Single-purpose                   |
| **PostgreSQL**   | Existing Postgres stack   | Manual implementation            |
| **Kafka**        | High-throughput streaming | Not ideal for per-stream queries |
| **DynamoDB**     | Serverless, AWS-native    | Query limitations                |
| **Marten**       | .NET ecosystems           | .NET specific                    |

## Templates and detailed worked examples

Full template library and detailed worked examples live in `references/details.md`. Read that file when you need the concrete templates.

## Best Practices

### Do's

- **Use stream IDs that include aggregate type** - `Order-{uuid}`
- **Include correlation/causation IDs** - For tracing
- **Version events from day one** - Plan for schema evolution
- **Implement idempotency** - Use event IDs for deduplication
- **Index appropriately** - For your query patterns

### Don'ts

- **Don't update or delete events** - They're immutable facts
- **Don't store large payloads** - Keep events small
- **Don't skip optimistic concurrency** - Prevents data corruption
- **Don't ignore backpressure** - Handle slow consumers
