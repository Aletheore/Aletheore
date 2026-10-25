---
name: refactor-plan
description: Create a concrete plan before starting a multi-file refactor. Use when the user asks to plan, sequence, scope, or safely execute a refactor across multiple files; always investigate first, output the plan, and wait for confirmation before making code changes.
skill_id: refactoring/refactor-plan
title: Refactor Plan
source_url: https://github.com/github/awesome-copilot/tree/cb64ad12e32737ae764becd6885462e9b4c7cb00/skills/refactor-plan
license: MIT
shelf: quality
aletheore: {"id": "refactoring/refactor-plan", "shelf": "quality", "category": "refactoring", "origin": "https://github.com/github/awesome-copilot/tree/cb64ad12e32737ae764becd6885462e9b4c7cb00/skills/refactor-plan", "license": "MIT", "license_source": "LICENSE", "upstream_commit": "cb64ad12e32737ae764becd6885462e9b4c7cb00", "quality": 3, "curation_note": "complexity reduction and refactor plans", "needs_tool": null, "vet": {"injection_pattern_hits": [], "portability_flags": [], "companion_files": 0, "has_scripts": false}, "curated_on": "2026-10-18", "requires": [], "adapted": {}, "status": "ready", "mentions_agents": []}
---

# Refactor Plan

Create a detailed plan before making any code changes.

## Instructions

1. Do not edit files while preparing the plan.
2. Search the codebase to understand the current state. Read enough implementation, tests, configuration, and docs to make the plan specific to the repository.
3. Identify affected files, ownership boundaries, dependencies, and likely hidden coupling.
4. Plan changes in a safe sequence. Prefer contracts and types first, then implementations, then callers, then tests, then cleanup.
5. Include verification steps between phases and a final validation command.
6. Include rollback or recovery steps for the riskiest phases.
7. Output the complete plan using the format below.
8. Stop after the plan and ask for confirmation before implementing. If the user already asked you to implement, still produce the plan first and wait for confirmation unless they explicitly said to continue without review after the plan.

If the request is too ambiguous to plan safely, ask concise clarifying questions instead of editing files.

## Output Format

```markdown
## Refactor Plan: [title]

### Current State
[Brief description of how things work now]

### Target State
[Brief description of how things will work after]

### Affected Files
| File | Change Type | Dependencies |
|------|-------------|--------------|
| path | modify/create/delete | blocks X, blocked by Y |

### Execution Plan

#### Phase 1: Types and Interfaces
- [ ] Step 1.1: [action] in `file.ts`
- [ ] Verify: [how to check it worked]

#### Phase 2: Implementation
- [ ] Step 2.1: [action] in `file.ts`
- [ ] Verify: [how to check]

#### Phase 3: Tests
- [ ] Step 3.1: Update tests in `file.test.ts`
- [ ] Verify: Run `npm test`

#### Phase 4: Cleanup
- [ ] Remove deprecated code
- [ ] Update documentation

### Rollback Plan
If something fails:
1. [Step to undo]
2. [Step to undo]

### Risks
- [Potential issue and mitigation]
```

After the plan, ask: "Shall I proceed with Phase 1?"
