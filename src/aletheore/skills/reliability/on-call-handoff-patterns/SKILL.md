---
name: on-call-handoff-patterns
description: Master on-call shift handoffs with context transfer, escalation procedures, and documentation. Use this skill when transitioning on-call responsibilities between engineers and ensuring the incoming responder has full situational awareness, when writing a shift summary that captures active incidents, ongoing investigations, and recent changes, when handing off mid-incident so a fresh engineer can take over the incident commander role without losing context, when onboarding a new engineer to the on-call rotation for the first time, or when auditing and improving the quality of existing handoff processes across teams.
skill_id: reliability/on-call-handoff-patterns
title: On Call Handoff Patterns
source_url: https://github.com/wshobson/agents/tree/0874a27b17a22e2b7c2e2bcd725768e0803cad06/plugins/incident-response/skills/on-call-handoff-patterns
license: MIT
shelf: ship-operate
aletheore: {"id": "reliability/on-call-handoff-patterns", "shelf": "ship-operate", "category": "reliability", "origin": "https://github.com/wshobson/agents/tree/0874a27b17a22e2b7c2e2bcd725768e0803cad06/plugins/incident-response/skills/on-call-handoff-patterns", "license": "MIT", "license_source": "LICENSE", "upstream_commit": "0874a27b17a22e2b7c2e2bcd725768e0803cad06", "quality": 3, "curation_note": "on-call handoff", "needs_tool": null, "vet": {"injection_pattern_hits": [], "portability_flags": [], "companion_files": 1, "has_scripts": false}, "curated_on": "2026-10-18", "requires": [], "adapted": {}, "status": "ready", "mentions_agents": []}
---

# On-Call Handoff Patterns

Effective patterns for on-call shift transitions, ensuring continuity, context transfer, and reliable incident response across shifts.

## When to Use This Skill

- Transitioning on-call responsibilities
- Writing shift handoff summaries
- Documenting ongoing investigations
- Establishing on-call rotation procedures
- Improving handoff quality
- Onboarding new on-call engineers

## Core Concepts

### 1. Handoff Components

| Component                  | Purpose                 |
| -------------------------- | ----------------------- |
| **Active Incidents**       | What's currently broken |
| **Ongoing Investigations** | Issues being debugged   |
| **Recent Changes**         | Deployments, configs    |
| **Known Issues**           | Workarounds in place    |
| **Upcoming Events**        | Maintenance, releases   |

### 2. Handoff Timing

```
Recommended: 30 min overlap between shifts

Outgoing:
├── 15 min: Write handoff document
└── 15 min: Sync call with incoming

Incoming:
├── 15 min: Review handoff document
├── 15 min: Sync call with outgoing
└── 5 min: Verify alerting setup
```

## Templates and detailed worked examples

Full template library and detailed worked examples live in `references/details.md`. Read that file when you need the concrete templates.

## Troubleshooting

**Incoming engineer misses a critical issue because the handoff document was incomplete.**
Use the outgoing checklist as a gate: do not mark handoff complete until every section has at least one entry (or an explicit "none"). Make incomplete handoffs a blameless postmortem action item.

**A 30-minute sync call is not possible due to timezone gaps.**
Fall back to the async quick handoff template (Template 2). Supplement with a short Loom or voice memo walking through the watch list. Ensure the incoming engineer has a direct contact method if they have follow-up questions.

**The incoming engineer inherits a mid-incident and is immediately overwhelmed.**
Use the incident handoff template (Template 3) specifically. The outgoing engineer should remain available on Slack for 15 minutes after handoff, even if off-call, to answer clarifying questions.

**On-call handoff documents are inconsistently formatted across teams.**
Adopt the shift handoff template organization-wide and store completed handoffs in a shared location (wiki, Notion, Confluence). Link each handoff from the on-call schedule entry in PagerDuty.

**Incoming engineer cannot verify their alerting is working before the outgoing engineer logs off.**
Add a standard step: outgoing engineer fires a test alert and confirms incoming engineer receives it in PagerDuty and Slack before ending the overlap window.

## Related Skills

- [incident-runbook-templates](../incident-runbook-templates/SKILL.md) — Runbooks for the incidents that are handed over mid-response
- [postmortem-writing](../postmortem-writing/SKILL.md) — Turn resolved incidents from the shift into structured postmortems
