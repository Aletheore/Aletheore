---
name: handoff
description: Compact the current conversation into a handoff document for another agent to pick up.
argument-hint: What will the next session be used for?
disable-model-invocation: true
skill_id: agent-workflows/handoff
title: Handoff
source_url: https://github.com/mattpocock/skills/tree/49dd158d1076134a641b33efb035946536778336/skills/productivity/handoff
license: MIT
shelf: ai-agents
aletheore: {"id": "agent-workflows/handoff", "shelf": "ai-agents", "category": "agent-workflows", "origin": "https://github.com/mattpocock/skills/tree/49dd158d1076134a641b33efb035946536778336/skills/productivity/handoff", "license": "MIT", "license_source": "LICENSE", "upstream_commit": "49dd158d1076134a641b33efb035946536778336", "quality": 4, "curation_note": "handoff document for another agent", "needs_tool": null, "vet": {"injection_pattern_hits": [], "portability_flags": ["claude-tools"], "companion_files": 1, "has_scripts": false}, "curated_on": "2026-10-18", "requires": [], "mentions_agents": [], "adapted": {"skill-tool": 1}, "status": "ready"}
---

Write a handoff document summarising the current conversation so a fresh agent can continue the work. Save to the temporary directory of the user's OS (`$TMPDIR`, else `/tmp`; `%TEMP%` on Windows) - not the current workspace.

Include a "suggested skills" section in the document, naming which skills the next agent should load.

Do not duplicate content already captured in other artifacts (specs, plans, ADRs, issues, commits, diffs). Reference them by path or URL instead.

Redact any sensitive information, such as API keys, passwords, or personally identifiable information.

If the user passed arguments, treat them as a description of what the next session will focus on and tailor the doc accordingly.
