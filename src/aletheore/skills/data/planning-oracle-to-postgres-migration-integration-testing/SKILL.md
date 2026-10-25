---
name: planning-oracle-to-postgres-migration-integration-testing
description: Creates an integration testing plan for .NET data access artifacts during Oracle-to-PostgreSQL database migrations. Analyzes a single project to identify repositories, DAOs, and service layers that interact with the database, then produces a structured testing plan. Use when planning integration test coverage for a migrated project, identifying which data access methods need tests, or preparing for Oracle-to-PostgreSQL migration validation.
skill_id: data/planning-oracle-to-postgres-migration-integration-testing
title: Planning Oracle to Postgres Migration Integration Testing
source_url: https://github.com/github/awesome-copilot/tree/cb64ad12e32737ae764becd6885462e9b4c7cb00/skills/planning-oracle-to-postgres-migration-integration-testing
license: MIT
shelf: build
aletheore: {"id": "data/planning-oracle-to-postgres-migration-integration-testing", "shelf": "build", "category": "data", "origin": "https://github.com/github/awesome-copilot/tree/cb64ad12e32737ae764becd6885462e9b4c7cb00/skills/planning-oracle-to-postgres-migration-integration-testing", "license": "MIT", "license_source": "LICENSE", "upstream_commit": "cb64ad12e32737ae764becd6885462e9b4c7cb00", "quality": 3, "curation_note": "integration test plan for DB migration", "needs_tool": null, "vet": {"injection_pattern_hits": [], "portability_flags": ["slash"], "companion_files": 0, "has_scripts": false}, "curated_on": "2026-10-18", "requires": [], "adapted": {}, "status": "ready", "mentions_agents": []}
---

# Planning Integration Testing for Oracle-to-PostgreSQL Migration

Analyze a single target project to identify data access artifacts that require integration testing, then produce a structured, actionable testing plan.

## Workflow

```
Progress:
- [ ] Step 1: Identify data access artifacts
- [ ] Step 2: Classify testing priorities
- [ ] Step 3: Write the testing plan
```

**Step 1: Identify data access artifacts**

Scope to the target project only. Find classes and methods that interact directly with the database — repositories, DAOs, stored procedure callers, service layers performing CRUD operations.

**Step 2: Classify testing priorities**

Rank artifacts by migration risk. Prioritize methods that use Oracle-specific features (refcursors, `TO_CHAR`, implicit type coercion, `NO_DATA_FOUND`) over simple CRUD.

**Step 3: Write the testing plan**

Write a markdown plan covering:
- List of testable artifacts with method signatures
- Recommended test cases per artifact
- Seed data requirements
- Known Oracle→PostgreSQL behavioral differences to validate
- Coverage mapping that ensures every database touchpoint has at least one test case (or a justified set of cases for high-risk methods)

When defining recommended test cases, explicitly include:
- Text parameter behavior for both empty string and `NULL`/missing values.
- Datetime/timezone assertions, including round-trip and comparison behavior.
- Cases where destination columns use `timestamp without time zone` or `timestamp(0)`, with explicit timezone-application expectations.

## Output

Write the plan to: `.github/oracle-to-postgres-migration/Reports/{TARGET_PROJECT} Integration Testing Plan.md`

## Key Constraints

- **Single project scope** — only plan tests for artifacts within the target project.
- **Database interactions only** — skip business logic that does not touch the database.
- **Oracle is the golden source** — tests should capture Oracle's expected behavior for comparison against PostgreSQL.
- **No multi-connection harnessing** — migrated applications are copied and renamed (e.g., `MyApp.Postgres`), so each instance targets one database.
