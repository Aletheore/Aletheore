---
name: notion-spec-to-implementation
description: Turn Notion specs into implementation plans, tasks, and progress tracking; use when implementing PRDs/feature specs and creating Notion plans + tasks from them.
metadata:
  short-description: Turn Notion specs into implementation plans, tasks, and progress tracking
skill_id: documents/notion-spec-to-implementation
title: Notion Spec to Implementation
source_url: https://github.com/openai/skills/tree/49f948faa9258a0c61caceaf225e179651397431/skills/.curated/notion-spec-to-implementation
license: MIT
shelf: content-media
aletheore: {"id": "documents/notion-spec-to-implementation", "shelf": "content-media", "category": "documents", "origin": "https://github.com/openai/skills/tree/49f948faa9258a0c61caceaf225e179651397431/skills/.curated/notion-spec-to-implementation", "license": "MIT", "license_source": "skills/.curated/notion-spec-to-implementation/LICENSE.txt", "upstream_commit": "49f948faa9258a0c61caceaf225e179651397431", "quality": 3, "curation_note": "Notion workflows; needs Notion MCP/connector", "needs_tool": "Notion MCP/connector", "vet": {"injection_pattern_hits": [], "portability_flags": [], "companion_files": 18, "has_scripts": false}, "curated_on": "2026-10-18", "requires": ["mcp:notion", "tool:notion-mcp-connector"], "adapted": {}, "status": "ready", "mentions_agents": ["Codex"], "mentions": ["claude", "agents"]}
---

# Spec to Implementation

Convert a Notion spec into linked implementation plans, tasks, and ongoing status updates.

## Quick start
1) Locate the spec with `Notion:notion-search`, then fetch it with `Notion:notion-fetch`.
2) Parse requirements and ambiguities using `reference/spec-parsing.md`.
3) Create a plan page with `Notion:notion-create-pages` (pick a template: quick vs. full).
4) Find the task database, confirm schema, then create tasks with `Notion:notion-create-pages`.
5) Link spec ↔ plan ↔ tasks; keep status current with `Notion:notion-update-page`.

## Workflow

### 0) If any MCP call fails because Notion MCP is not connected, pause and set it up:
1. Add the Notion MCP:
   - `codex mcp add notion --url https://mcp.notion.com/mcp`
2. Enable remote MCP client:
   - Set `[features].rmcp_client = true` in `config.toml` **or** run `codex --enable rmcp_client`
3. Log in with OAuth:
   - `codex mcp login notion`

After successful login, the user will have to restart codex. You should finish your answer and tell them so when they try again they can continue with Step 1.

### 1) Locate and read the spec
- Search first (`Notion:notion-search`); if multiple hits, ask the user which to use.
- Fetch the page (`Notion:notion-fetch`) and scan for requirements, acceptance criteria, constraints, and priorities. See `reference/spec-parsing.md` for extraction patterns.
- Capture gaps/assumptions in a clarifications block before proceeding.

### 2) Choose plan depth
- Simple change → use `reference/quick-implementation-plan.md`.
- Multi-phase feature/migration → use `reference/standard-implementation-plan.md`.
- Create the plan via `Notion:notion-create-pages`, include: overview, linked spec, requirements summary, phases, dependencies/risks, and success criteria. Link back to the spec.

### 3) Create tasks
- Find the task database (`Notion:notion-search` → `Notion:notion-fetch` to confirm the data source and required properties). Patterns in `reference/task-creation.md`.
- Size tasks to 1–2 days. Use `reference/task-creation-template.md` for content (context, objective, acceptance criteria, dependencies, resources).
- Set properties: title/action verb, status, priority, relations to spec + plan, due date/story points/assignee if provided.
- Create pages with `Notion:notion-create-pages` using the database’s `data_source_id`.

### 4) Link artifacts
- Plan links to spec; tasks link to both plan and spec.
- Optionally update the spec with a short “Implementation” section pointing to the plan and tasks using `Notion:notion-update-page`.

### 5) Track progress
- Use the cadence in `reference/progress-tracking.md`.
- Post updates with `reference/progress-update-template.md`; close phases with `reference/milestone-summary-template.md`.
- Keep checklists and status fields in plan/tasks in sync; note blockers and decisions.

## References and examples
- `reference/` — parsing patterns, plan/task templates, progress cadence (e.g., `spec-parsing.md`, `standard-implementation-plan.md`, `task-creation.md`, `progress-tracking.md`).
- `examples/` — end-to-end walkthroughs (e.g., `ui-component.md`, `api-feature.md`, `database-migration.md`).
