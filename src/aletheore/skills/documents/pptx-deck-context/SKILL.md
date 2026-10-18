---
name: pptx-deck-context
description: Use when preparing the narrative, sources, and design context for a new editable PPTX deck.
skill_id: documents/pptx-deck-context
title: Pptx Deck Context
source_url: https://github.com/wshobson/agents/tree/0874a27b17a22e2b7c2e2bcd725768e0803cad06/plugins/pptx-deck-creation/skills/pptx-deck-context
license: MIT
shelf: content-media
aletheore: {"id": "documents/pptx-deck-context", "shelf": "content-media", "category": "documents", "origin": "https://github.com/wshobson/agents/tree/0874a27b17a22e2b7c2e2bcd725768e0803cad06/plugins/pptx-deck-creation/skills/pptx-deck-context", "license": "MIT", "license_source": "LICENSE", "upstream_commit": "0874a27b17a22e2b7c2e2bcd725768e0803cad06", "quality": 3, "curation_note": "pptx deck pipeline; needs pptx tooling", "needs_tool": "pptx tooling", "vet": {"injection_pattern_hits": [], "portability_flags": [], "companion_files": 1, "has_scripts": false}, "curated_on": "2026-10-18", "requires": ["tool:pptx-tooling"], "adapted": {}, "status": "ready", "mentions_agents": []}
---

# PPTX Deck Context

Prepare the deck before coordinates or PPTX objects are authored. This skill owns the business narrative, source lineage, and design lock.

## Decision sequence

1. Confirm audience, decision, slide count, language, sources, and brand requirements.
2. Use the user-selected narrative framework. If none is specified, offer `mckinsey`, `scqa`, `pyramid`, `mece`, `action-title`, `assertion-evidence`, `exec-summary-first`, or `custom`; do not choose silently.
3. Assign stable source IDs and plan a `source_ref` for every metric, quotation, chart value, and factual claim.
4. Choose a documented design direction. Prefer a user brand guide, then read-only reference-deck evidence, then a reusable profile from `references/design-profiles.md`.
5. Record the framework, assumptions, source manifest, palette, typography, spacing, and signature elements in `summary` before authoring slides.

## Rules

- One slide should communicate one message, with an action-style title where the framework calls for it.
- Treat design references as design evidence, not content or asset sources.
- Do not copy external fonts, images, icons, or logos without recorded licensing evidence.
- Translate design signals into explicit fills, typography, spacing, and bboxes; do not rely on an automatic layout engine.
- Keep long source material concise. Ask for a summary or decision-relevant excerpt instead of turning a deck spec into a document dump.

See `references/design-profiles.md` for reusable profile guidance.
