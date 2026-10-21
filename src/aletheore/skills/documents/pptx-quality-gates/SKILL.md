---
name: pptx-quality-gates
description: Use when validating or repairing an editable PPTX deck for geometry, accessibility, native editability, source lineage, and OOXML package integrity.
skill_id: documents/pptx-quality-gates
title: Pptx Quality Gates
source_url: https://github.com/wshobson/agents/tree/0874a27b17a22e2b7c2e2bcd725768e0803cad06/plugins/pptx-deck-creation/skills/pptx-quality-gates
license: MIT
shelf: content-media
aletheore: {"id": "documents/pptx-quality-gates", "shelf": "content-media", "category": "documents", "origin": "https://github.com/wshobson/agents/tree/0874a27b17a22e2b7c2e2bcd725768e0803cad06/plugins/pptx-deck-creation/skills/pptx-quality-gates", "license": "MIT", "license_source": "LICENSE", "upstream_commit": "0874a27b17a22e2b7c2e2bcd725768e0803cad06", "quality": 3, "curation_note": "pptx deck pipeline; needs pptx tooling", "needs_tool": "pptx tooling", "vet": {"injection_pattern_hits": [], "portability_flags": [], "companion_files": 1, "has_scripts": false}, "curated_on": "2026-10-18", "requires": ["tool:pptx-tooling"], "adapted": {}, "status": "ready", "mentions_agents": []}
---

# PPTX Quality Gates

Use this skill before delivering a generated PPTX. Treat deterministic failures as repair work, not exceptions.

## Workflow

1. Confirm the spec, builder, PPTX, source manifest, and audit paths.
2. Apply `references/audit-checklist.md` to the coordinate contract before building.
3. Reopen the PPTX and compare slide count, actual bounds, hidden slides, and requested geometry with the layout tree.
4. For production decks, run `pptx-reference-deck-analysis/scripts/validate_package.py` and save the JSON report.
5. Check document language, slide titles, meaningful-image alt text, reading order, table headers, and source references.
6. When a compatible renderer is already available, inspect previews for clipping, font fallback, contrast, crop, and hierarchy. Do not add a renderer dependency.
7. Repair the spec or task-local builder, rebuild, and rerun the same checks.

## Required artifacts

Keep the spec, PPTX, build record, geometry audit, accessibility audit, package report, and source manifest together. Report any documented exception with slide ID, object ID, owner, reason, and review date.

## Pass criteria

Zero content collisions, text overflows, unsafe content bounds, invalid positive geometry, and package-integrity errors. Meaningful slide content must remain native editable objects; images support rather than replace it.

See `references/audit-checklist.md` for the complete checklist.
