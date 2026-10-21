---
name: notion-meeting-intelligence
description: Prepare meeting materials with Notion context and Codex research; use when gathering context, drafting agendas/pre-reads, and tailoring materials to attendees.
metadata:
  short-description: Prep meetings with Notion context and tailored agendas
skill_id: documents/notion-meeting-intelligence
title: Notion Meeting Intelligence
source_url: https://github.com/openai/skills/tree/49f948faa9258a0c61caceaf225e179651397431/skills/.curated/notion-meeting-intelligence
license: MIT
shelf: content-media
aletheore: {"id": "documents/notion-meeting-intelligence", "shelf": "content-media", "category": "documents", "origin": "https://github.com/openai/skills/tree/49f948faa9258a0c61caceaf225e179651397431/skills/.curated/notion-meeting-intelligence", "license": "MIT", "license_source": "skills/.curated/notion-meeting-intelligence/LICENSE.txt", "upstream_commit": "49f948faa9258a0c61caceaf225e179651397431", "quality": 3, "curation_note": "Notion workflows; needs Notion MCP/connector", "needs_tool": "Notion MCP/connector", "vet": {"injection_pattern_hits": [], "portability_flags": [], "companion_files": 18, "has_scripts": false}, "curated_on": "2026-10-18", "requires": ["mcp:notion", "tool:notion-mcp-connector"], "mentions_agents": ["Codex"], "mentions": ["claude", "agents"], "adapted": {}, "status": "ready"}
---

# Meeting Intelligence

Prep meetings by pulling Notion context, tailoring agendas/pre-reads, and enriching with Codex research.

## Quick start
1) Confirm meeting goal, attendees, date/time, and decisions needed.
2) Gather context: search with `Notion:notion-search`, then fetch with `Notion:notion-fetch` (prior notes, specs, OKRs, decisions).
3) Pick the right template via `reference/template-selection-guide.md` (status, decision, planning, retro, 1:1, brainstorming).
4) Draft agenda/pre-read in Notion with `Notion:notion-create-pages`, embedding source links and owner/timeboxes.
5) Enrich with Codex research (industry insights, benchmarks, risks) and update the page with `Notion:notion-update-page` as plans change.

## Workflow
### 0) If any MCP call fails because Notion MCP is not connected, pause and set it up:
1. Add the Notion MCP:
   - `codex mcp add notion --url https://mcp.notion.com/mcp`
2. Enable remote MCP client:
   - Set `[features].rmcp_client = true` in `config.toml` **or** run `codex --enable rmcp_client`
3. Log in with OAuth:
   - `codex mcp login notion`

After successful login, the user will have to restart codex. You should finish your answer and tell them so when they try again they can continue with Step 1.

### 1) Gather inputs
- Ask for objective, desired outcomes/decisions, attendees, duration, date/time, and prior materials.
- Search Notion for relevant docs, past notes, specs, and action items (`Notion:notion-search`), then fetch key pages (`Notion:notion-fetch`).
- Capture blockers/risks and open questions up front.

### 2) Choose format
- Status/update → status template.
- Decision/approval → decision template.
- Planning (sprint/project) → planning template.
- Retro/feedback → retrospective template.
- 1:1 → one-on-one template.
- Ideation → brainstorming template.
- Use `reference/template-selection-guide.md` to confirm.

### 3) Build the agenda/pre-read
- Start from the chosen template in `reference/` and adapt sections (context, goals, agenda, owner/time per item, decisions, risks, prep asks).
- Include links to pulled Notion pages and any required pre-reading.
- Assign owners for each agenda item; call out timeboxes and expected outputs.

### 4) Enrich with research
- Add concise Codex research where helpful: market/industry facts, benchmarks, risks, best practices.
- Keep claims cited with source links; separate fact from opinion.

### 5) Finalize and share
- Add next steps and owners for follow-ups.
- If tasks arise, create/link tasks in the relevant Notion database.
- Update the page via `Notion:notion-update-page` when details change; keep a brief changelog if multiple edits.

## References and examples
- `reference/` — template picker and meeting templates (e.g., `template-selection-guide.md`, `status-update-template.md`, `decision-meeting-template.md`, `sprint-planning-template.md`, `one-on-one-template.md`, `retrospective-template.md`, `brainstorming-template.md`).
- `examples/` — end-to-end meeting preps (e.g., `executive-review.md`, `project-decision.md`, `sprint-planning.md`, `customer-meeting.md`).
