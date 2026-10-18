---
name: react-audit-grep-patterns
description: Provides the complete, verified grep scan command library for auditing React codebases before a React 18.3.1 or React 19 upgrade. Use this skill whenever running a migration audit - for both the react18-auditor and react19-auditor agents. Contains every grep pattern needed to find deprecated APIs, removed APIs, unsafe lifecycle methods, batching vulnerabilities, test file issues, dependency conflicts, and React 19 specific removals. Always use this skill when writing audit scan commands - do not rely on memory for grep syntax, especially for the multi-line async setState patterns which require context flags.
skill_id: frontend/react-audit-grep-patterns
title: React Audit Grep Patterns
source_url: https://github.com/github/awesome-copilot/tree/cb64ad12e32737ae764becd6885462e9b4c7cb00/skills/react-audit-grep-patterns
license: MIT
shelf: build
aletheore: {"id": "frontend/react-audit-grep-patterns", "shelf": "build", "category": "frontend", "origin": "https://github.com/github/awesome-copilot/tree/cb64ad12e32737ae764becd6885462e9b4c7cb00/skills/react-audit-grep-patterns", "license": "MIT", "license_source": "LICENSE", "upstream_commit": "cb64ad12e32737ae764becd6885462e9b4c7cb00", "quality": 3, "curation_note": "React audit grep patterns", "needs_tool": null, "vet": {"injection_pattern_hits": [], "portability_flags": [], "companion_files": 4, "has_scripts": false}, "curated_on": "2026-10-18", "requires": [], "adapted": {}, "status": "ready", "mentions_agents": []}
---

# React Audit Grep Patterns

Complete scan command library for React 18.3.1 and React 19 migration audits.

## Usage

Read the relevant section for your target:
- **`references/react18-scans.md`** - all scans for React 16/17 → 18.3.1 audit
- **`references/react19-scans.md`** - all scans for React 18 → 19 audit
- **`references/test-scans.md`** - test file specific scans (used by both auditors)
- **`references/dep-scans.md`** - dependency and peer conflict scans

## Base Patterns Used Across All Scans

```bash
# Standard flags used throughout:
# -r = recursive
# -n = show line numbers
# -l = show filenames only (for counting affected files)
# --include="*.js" --include="*.jsx" = JS/JSX files only
# | grep -v "\.test\.\|\.spec\.\|__tests__" = exclude test files
# | grep -v "node_modules" = safety (usually handled by not scanning node_modules)
# 2>/dev/null = suppress "no files found" errors

# Source files only (exclude tests):
SRC_FLAGS='--include="*.js" --include="*.jsx"'
EXCLUDE_TESTS='grep -v "\.test\.\|\.spec\.\|__tests__"'

# Test files only:
TEST_FLAGS='--include="*.test.js" --include="*.test.jsx" --include="*.spec.js" --include="*.spec.jsx"'
```
