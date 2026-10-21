---
name: bazel-build-optimization
description: Optimize Bazel builds for large-scale monorepos. Use when configuring Bazel, implementing remote execution, or optimizing build performance for enterprise codebases.
skill_id: devops/bazel-build-optimization
title: Bazel Build Optimization
source_url: https://github.com/wshobson/agents/tree/0874a27b17a22e2b7c2e2bcd725768e0803cad06/plugins/developer-essentials/skills/bazel-build-optimization
license: MIT
shelf: ship-operate
aletheore: {"id": "devops/bazel-build-optimization", "shelf": "ship-operate", "category": "devops", "origin": "https://github.com/wshobson/agents/tree/0874a27b17a22e2b7c2e2bcd725768e0803cad06/plugins/developer-essentials/skills/bazel-build-optimization", "license": "MIT", "license_source": "LICENSE", "upstream_commit": "0874a27b17a22e2b7c2e2bcd725768e0803cad06", "quality": 3, "curation_note": "bazel", "needs_tool": null, "vet": {"injection_pattern_hits": [], "portability_flags": [], "companion_files": 1, "has_scripts": false}, "curated_on": "2026-10-18", "requires": [], "adapted": {}, "status": "ready", "mentions_agents": []}
---

# Bazel Build Optimization

Production patterns for Bazel in large-scale monorepos.

## When to Use This Skill

- Setting up Bazel for monorepos
- Configuring remote caching/execution
- Optimizing build times
- Writing custom Bazel rules
- Debugging build issues
- Migrating to Bazel

## Core Concepts

### 1. Bazel Architecture

```
workspace/
├── WORKSPACE.bazel       # External dependencies
├── .bazelrc              # Build configurations
├── .bazelversion         # Bazel version
├── BUILD.bazel           # Root build file
├── apps/
│   └── web/
│       └── BUILD.bazel
├── libs/
│   └── utils/
│       └── BUILD.bazel
└── tools/
    └── bazel/
        └── rules/
```

### 2. Key Concepts

| Concept     | Description                            |
| ----------- | -------------------------------------- |
| **Target**  | Buildable unit (library, binary, test) |
| **Package** | Directory with BUILD file              |
| **Label**   | Target identifier `//path/to:target`   |
| **Rule**    | Defines how to build a target          |
| **Aspect**  | Cross-cutting build behavior           |

## Templates and detailed worked examples

Full template library and detailed worked examples live in `references/details.md`. Read that file when you need the concrete templates.

## Best Practices

### Do's

- **Use fine-grained targets** - Better caching
- **Pin dependencies** - Reproducible builds
- **Enable remote caching** - Share build artifacts
- **Use visibility wisely** - Enforce architecture
- **Write BUILD files per directory** - Standard convention

### Don'ts

- **Don't use glob for deps** - Explicit is better
- **Don't commit bazel-\* dirs** - Add to .gitignore
- **Don't skip WORKSPACE setup** - Foundation of build
- **Don't ignore build warnings** - Technical debt
