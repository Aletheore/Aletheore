---
name: safe-refactor
description: Restructure code while preserving behavior. Use for extraction, consolidation, ownership moves, or cleanup where verification must bracket structural edits.
skill_id: refactoring/safe-refactor
title: Safe Refactor
source_url: https://github.com/JuliusBrussee/caveman/tree/bad48a0e1df55e05e3f52249afa35ecc1909ee76/skills/safe-refactor
license: Apache-2.0
shelf: quality
aletheore: {"id": "refactoring/safe-refactor", "shelf": "quality", "category": "refactoring", "origin": "https://github.com/JuliusBrussee/caveman/tree/bad48a0e1df55e05e3f52249afa35ecc1909ee76/skills/safe-refactor", "license": "Apache-2.0", "license_source": "LICENSE", "upstream_commit": "bad48a0e1df55e05e3f52249afa35ecc1909ee76", "quality": 3, "curation_note": "behavior-preserving restructure", "needs_tool": null, "vet": {"injection_pattern_hits": [], "portability_flags": [], "companion_files": 1, "has_scripts": false}, "curated_on": "2026-10-18", "requires": [], "adapted": {}, "status": "ready", "mentions_agents": []}
---

# Safe refactor

Define behavior-preservation boundary and establish verification before structural edits.

- Keep feature changes outside refactor.
- Move one ownership boundary at a time.
- Preserve public interfaces, failure behavior, ordering, and compatibility unless explicitly scoped.
- Keep intermediate states buildable and testable.
- Avoid dependency or configuration growth without correctness need.

Run same proof after change. Stop when behavior matches and requested structure is achieved.
