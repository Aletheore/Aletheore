---
name: reviewing-oracle-to-postgres-migration
description: Identifies Oracle-to-PostgreSQL migration risks by cross-referencing code against known behavioral differences (empty strings, refcursors, type coercion, sorting/collations, UNION ALL planner risks, materialized-view refresh requirements, timestamps, concurrent transactions, etc.). Use when planning a database migration, reviewing migration artifacts, or validating that integration tests cover Oracle/PostgreSQL differences.
skill_id: data/reviewing-oracle-to-postgres-migration
title: Reviewing Oracle to Postgres Migration
source_url: https://github.com/github/awesome-copilot/tree/cb64ad12e32737ae764becd6885462e9b4c7cb00/skills/reviewing-oracle-to-postgres-migration
license: MIT
shelf: build
aletheore: {"id": "data/reviewing-oracle-to-postgres-migration", "shelf": "build", "category": "data", "origin": "https://github.com/github/awesome-copilot/tree/cb64ad12e32737ae764becd6885462e9b4c7cb00/skills/reviewing-oracle-to-postgres-migration", "license": "MIT", "license_source": "LICENSE", "upstream_commit": "cb64ad12e32737ae764becd6885462e9b4c7cb00", "quality": 3, "curation_note": "migration risk review", "needs_tool": null, "vet": {"injection_pattern_hits": [], "portability_flags": [], "companion_files": 15, "has_scripts": false}, "curated_on": "2026-10-18", "requires": [], "adapted": {}, "status": "ready", "mentions_agents": []}
---

# Oracle-to-PostgreSQL Database Migration

Surfaces migration risks and validates migration work against known Oracle/PostgreSQL behavioral differences documented in the `references/` folder.

## When to use

1. **Planning** — Before starting migration work on a procedure, trigger, query, or refcursor client. Identify which reference insights apply so risks are addressed up front.
2. **Validating** — After migration work is done, confirm every applicable insight was addressed and integration tests cover the new PostgreSQL semantics.

## Workflow

Determine the task type:

**Planning a migration?** Follow the risk assessment workflow.
**Validating completed work?** Follow the validation workflow.

### Risk assessment workflow (planning)

```
Risk Assessment:
- [ ] Step 1: Identify the migration scope
- [ ] Step 2: Screen each insight for applicability
- [ ] Step 3: Document risks and recommended actions
```

**Step 1: Identify the migration scope**

List the affected database objects (procedures, triggers, queries, views) and the application code that calls them.

**Step 2: Screen each insight for applicability**

Review the reference index in [references/REFERENCE.md](references/REFERENCE.md). For each entry, determine whether the migration scope contains patterns affected by that insight. Read the full reference file only when the insight is potentially relevant.

**Step 3: Document risks and recommended actions**

For each applicable insight, note the specific risk and the recommended fix pattern from the reference file. Flag any insight that requires a design decision (e.g., whether to preserve Oracle empty-string-as-NULL semantics or adopt PostgreSQL behavior).

### Validation workflow (post-migration)

```
Validation:
- [ ] Step 1: Map the migration artifact
- [ ] Step 2: Cross-check applicable insights
- [ ] Step 3: Verify integration test coverage
- [ ] Step 4: Gate the result
```

**Step 1: Map the migration artifact**

Identify the migrated object and summarize the change set.

**Step 2: Cross-check applicable insights**

For each reference in [references/REFERENCE.md](references/REFERENCE.md), confirm the behavior or test requirement is acknowledged and addressed in the migration work.

**Step 3: Verify integration test coverage**

Confirm tests exercise both the happy path and the failure scenarios highlighted in applicable insights (exceptions, sorting, `UNION ALL` behavior/performance risks, refcursor consumption, concurrent transactions, timestamps, materialized-view freshness, etc.).

**Step 4: Gate the result**

Return a checklist asserting each applicable insight was addressed, migration scripts run, and integration tests pass.
