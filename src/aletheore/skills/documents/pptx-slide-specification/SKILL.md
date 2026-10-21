---
name: pptx-slide-specification
description: Use when authoring or repairing a coordinate-explicit JSON specification for an editable PPTX deck.
skill_id: documents/pptx-slide-specification
title: Pptx Slide Specification
source_url: https://github.com/wshobson/agents/tree/0874a27b17a22e2b7c2e2bcd725768e0803cad06/plugins/pptx-deck-creation/skills/pptx-slide-specification
license: MIT
shelf: content-media
aletheore: {"id": "documents/pptx-slide-specification", "shelf": "content-media", "category": "documents", "origin": "https://github.com/wshobson/agents/tree/0874a27b17a22e2b7c2e2bcd725768e0803cad06/plugins/pptx-deck-creation/skills/pptx-slide-specification", "license": "MIT", "license_source": "LICENSE", "upstream_commit": "0874a27b17a22e2b7c2e2bcd725768e0803cad06", "quality": 3, "curation_note": "pptx deck pipeline; needs pptx tooling", "needs_tool": "pptx tooling", "vet": {"injection_pattern_hits": [], "portability_flags": [], "companion_files": 1, "has_scripts": false}, "curated_on": "2026-10-18", "requires": ["tool:pptx-tooling"], "adapted": {}, "status": "ready", "mentions_agents": []}
---

# PPTX Slide Specification

Author final coordinates directly in `layout_tree`. No renderer may decide placement, shrink text, or infer layout after the audit.

## Required contract

- The root JSON contains `summary` and `slides`.
- Every slide has a stable `id`, a message-led `title`, an accessibility reading order, and a complete `layout_tree`.
- A layout tree declares slide size, root group, id-keyed groups and objects, final inch bboxes, styles, z-indexes, and classifications.
- Every meaningful object has `id`, `kind`, `role`, `classification`, `content`, `style`, `bbox`, and `z_index`.
- Production `summary` contains an explicit `layout_policy` (safe margin, content bottom, footer top, minimum gap) and accessibility metadata.

## Authoring rules

1. Use stable readable IDs, absolute groups, and positive inch dimensions.
2. Keep normal content inside the safe margin and above the footer rail. Only background `layout_design` objects may be full bleed.
3. Use native `text`, `shape`, `line`, `table`, and `image` objects. Recreate labels and values from any supporting visual as native objects.
4. Give meaningful images alt text. Record `source_ref` for sourced claims.
5. Keep content text at 9 pt or greater; shorten, resize, or split dense content before reducing type.
6. Use shared grids, consistent gaps, explicit color, explicit font size, and an intentional z-order.

## Build contract

A task-local builder starts from a blank slide layout and maps all bboxes with `Inches(...)`. Explicitly set wrapping, disabled auto-size, text insets, anchors, alignment, fonts, colors, line settings, image aspect ratio, and hidden-slide state. Reject zero or negative geometry before adding an object.

See `references/layout-contract.md` for the compact schema and repair guidance.
