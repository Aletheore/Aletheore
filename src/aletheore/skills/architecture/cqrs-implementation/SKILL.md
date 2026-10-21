---
name: cqrs-implementation
description: Implement Command Query Responsibility Segregation for scalable architectures. Use when separating read and write models, optimizing query performance, or building event-sourced systems.
skill_id: architecture/cqrs-implementation
title: Cqrs Implementation
source_url: https://github.com/wshobson/agents/tree/0874a27b17a22e2b7c2e2bcd725768e0803cad06/plugins/backend-development/skills/cqrs-implementation
license: MIT
shelf: build
aletheore: {"id": "architecture/cqrs-implementation", "shelf": "build", "category": "architecture", "origin": "https://github.com/wshobson/agents/tree/0874a27b17a22e2b7c2e2bcd725768e0803cad06/plugins/backend-development/skills/cqrs-implementation", "license": "MIT", "license_source": "LICENSE", "upstream_commit": "0874a27b17a22e2b7c2e2bcd725768e0803cad06", "quality": 3, "curation_note": "CQRS/microservices/projection/saga", "needs_tool": null, "vet": {"injection_pattern_hits": [], "portability_flags": [], "companion_files": 1, "has_scripts": false}, "curated_on": "2026-10-18", "requires": [], "adapted": {}, "status": "ready", "mentions_agents": []}
---

# CQRS Implementation

Comprehensive guide to implementing CQRS (Command Query Responsibility Segregation) patterns.

## When to Use This Skill

- Separating read and write concerns
- Scaling reads independently from writes
- Building event-sourced systems
- Optimizing complex query scenarios
- Different read/write data models needed
- High-performance reporting requirements

## Core Concepts

### 1. CQRS Architecture

```
                    ┌─────────────┐
                    │   Client    │
                    └──────┬──────┘
                           │
              ┌────────────┴────────────┐
              │                         │
              ▼                         ▼
       ┌─────────────┐          ┌─────────────┐
       │  Commands   │          │   Queries   │
       │    API      │          │    API      │
       └──────┬──────┘          └──────┬──────┘
              │                         │
              ▼                         ▼
       ┌─────────────┐          ┌─────────────┐
       │  Command    │          │   Query     │
       │  Handlers   │          │  Handlers   │
       └──────┬──────┘          └──────┬──────┘
              │                         │
              ▼                         ▼
       ┌─────────────┐          ┌─────────────┐
       │   Write     │─────────►│    Read     │
       │   Model     │  Events  │   Model     │
       └─────────────┘          └─────────────┘
```

### 2. Key Components

| Component           | Responsibility                  |
| ------------------- | ------------------------------- |
| **Command**         | Intent to change state          |
| **Command Handler** | Validates and executes commands |
| **Event**           | Record of state change          |
| **Query**           | Request for data                |
| **Query Handler**   | Retrieves data from read model  |
| **Projector**       | Updates read model from events  |

## Templates and detailed worked examples

Full template library and detailed worked examples live in `references/details.md`. Read that file when you need the concrete templates.

## Best Practices

### Do's

- **Separate command and query models** - Different needs
- **Use eventual consistency** - Accept propagation delay
- **Validate in command handlers** - Before state change
- **Denormalize read models** - Optimize for queries
- **Version your events** - For schema evolution

### Don'ts

- **Don't query in commands** - Use only for writes
- **Don't couple read/write schemas** - Independent evolution
- **Don't over-engineer** - Start simple
- **Don't ignore consistency SLAs** - Define acceptable lag
