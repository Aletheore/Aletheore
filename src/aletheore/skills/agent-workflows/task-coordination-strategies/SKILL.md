---
name: task-coordination-strategies
description: Decompose complex tasks, design dependency graphs, and coordinate multi-agent work with proper task descriptions and workload balancing. Use this skill when breaking down work for agent teams, managing task dependencies, or monitoring team progress.
version: 1.0.2
skill_id: agent-workflows/task-coordination-strategies
title: Task Coordination Strategies
source_url: https://github.com/wshobson/agents/tree/0874a27b17a22e2b7c2e2bcd725768e0803cad06/plugins/agent-teams/skills/task-coordination-strategies
license: MIT
shelf: ai-agents
aletheore: {"id": "agent-workflows/task-coordination-strategies", "shelf": "ai-agents", "category": "agent-workflows", "origin": "https://github.com/wshobson/agents/tree/0874a27b17a22e2b7c2e2bcd725768e0803cad06/plugins/agent-teams/skills/task-coordination-strategies", "license": "MIT", "license_source": "LICENSE", "upstream_commit": "0874a27b17a22e2b7c2e2bcd725768e0803cad06", "quality": 3, "curation_note": "parallel work coordination", "needs_tool": null, "vet": {"injection_pattern_hits": [], "portability_flags": [], "companion_files": 2, "has_scripts": false}, "curated_on": "2026-10-18", "requires": [], "adapted": {}, "status": "ready", "mentions_agents": []}
---

# Task Coordination Strategies

Strategies for decomposing complex tasks into parallelizable units, designing dependency graphs, writing effective task descriptions, and monitoring workload across agent teams.

## When to Use This Skill

- Breaking down a complex task for parallel execution
- Designing task dependency relationships (blockedBy/blocks)
- Writing task descriptions with clear acceptance criteria
- Monitoring and rebalancing workload across teammates
- Identifying the critical path in a multi-task workflow

## Task Decomposition Strategies

### By Layer

Split work by architectural layer:

- Frontend components
- Backend API endpoints
- Database migrations/models
- Test suites

**Best for**: Full-stack features, vertical slices

### By Component

Split work by functional component:

- Authentication module
- User profile module
- Notification module

**Best for**: Microservices, modular architectures

### By Concern

Split work by cross-cutting concern:

- Security review
- Performance review
- Architecture review

**Best for**: Code reviews, audits

### By File Ownership

Split work by file/directory boundaries:

- `src/components/` — Implementer 1
- `src/api/` — Implementer 2
- `src/utils/` — Implementer 3

**Best for**: Parallel implementation, conflict avoidance

## Dependency Graph Design

### Principles

1. **Minimize chain depth** — Prefer wide, shallow graphs over deep chains
2. **Identify the critical path** — The longest chain determines minimum completion time
3. **Use blockedBy sparingly** — Only add dependencies that are truly required
4. **Avoid circular dependencies** — Task A blocks B blocks A is a deadlock

### Patterns

**Independent (Best parallelism)**:

```
Task A ─┐
Task B ─┼─→ Integration
Task C ─┘
```

**Sequential (Necessary dependencies)**:

```
Task A → Task B → Task C
```

**Diamond (Mixed)**:

```
        ┌→ Task B ─┐
Task A ─┤          ├→ Task D
        └→ Task C ─┘
```

### Using blockedBy/blocks

```
TaskCreate: { subject: "Build API endpoints" }         → Task #1
TaskCreate: { subject: "Build frontend components" }    → Task #2
TaskCreate: { subject: "Integration testing" }          → Task #3
TaskUpdate: { taskId: "3", addBlockedBy: ["1", "2"] }  → #3 waits for #1 and #2
```

## Task Description Best Practices

Every task should include:

1. **Objective** — What needs to be accomplished (1-2 sentences)
2. **Owned Files** — Explicit list of files/directories this teammate may modify
3. **Requirements** — Specific deliverables or behaviors expected
4. **Interface Contracts** — How this work connects to other teammates' work
5. **Acceptance Criteria** — How to verify the task is done correctly
6. **Scope Boundaries** — What is explicitly out of scope

### Template

```
## Objective
Build the user authentication API endpoints.

## Owned Files
- src/api/auth.ts
- src/api/middleware/auth-middleware.ts
- src/types/auth.ts (shared — read only, do not modify)

## Requirements
- POST /api/login — accepts email/password, returns JWT
- POST /api/register — creates new user, returns JWT
- GET /api/me — returns current user profile (requires auth)

## Interface Contract
- Import User type from src/types/auth.ts (owned by implementer-1)
- Export AuthResponse type for frontend consumption

## Acceptance Criteria
- All endpoints return proper HTTP status codes
- JWT tokens expire after 24 hours
- Passwords are hashed with bcrypt

## Out of Scope
- OAuth/social login
- Password reset flow
- Rate limiting
```

## Workload Monitoring

### Indicators of Imbalance

| Signal                     | Meaning             | Action                      |
| -------------------------- | ------------------- | --------------------------- |
| Teammate idle, others busy | Uneven distribution | Reassign pending tasks      |
| Teammate stuck on one task | Possible blocker    | Check in, offer help        |
| All tasks blocked          | Dependency issue    | Resolve critical path first |
| One teammate has 3x others | Overloaded          | Split tasks or reassign     |

### Rebalancing Steps

1. Call `TaskList` to assess current state
2. Identify idle or overloaded teammates
3. Use `TaskUpdate` to reassign tasks
4. Use `SendMessage` to notify affected teammates
5. Monitor for improved throughput
