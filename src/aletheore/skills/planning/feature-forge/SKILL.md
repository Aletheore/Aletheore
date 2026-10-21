---
name: feature-forge
description: Conducts structured requirements workshops to produce feature specifications, user stories, EARS-format functional requirements, acceptance criteria, and implementation checklists. Use when defining new features, gathering requirements, or writing specifications. Invoke for feature definition, requirements gathering, user stories, EARS format specs, PRDs, acceptance criteria, or requirement matrices.
license: MIT
metadata:
  author: https://github.com/Jeffallan
  company: https://synergetic.solutions
  version: 1.1.0
  domain: workflow
  triggers: requirements, specification, feature definition, user stories, EARS, planning
  role: specialist
  scope: design
  output-format: document
  related-skills: fullstack-guardian, spec-miner, test-master
skill_id: planning/feature-forge
title: Feature Forge
source_url: https://github.com/Jeffallan/claude-skills/tree/36f3fe8052da7c57083783268c6ea3265d075265/skills/feature-forge
shelf: build
aletheore: {"id": "planning/feature-forge", "shelf": "build", "category": "planning", "origin": "https://github.com/Jeffallan/claude-skills/tree/36f3fe8052da7c57083783268c6ea3265d075265/skills/feature-forge", "license": "MIT", "license_source": "frontmatter", "upstream_commit": "36f3fe8052da7c57083783268c6ea3265d075265", "quality": 4, "curation_note": "requirements workshop to spec", "needs_tool": null, "vet": {"injection_pattern_hits": [], "portability_flags": [], "companion_files": 5, "has_scripts": false}, "curated_on": "2026-10-18", "requires": ["subagents"], "mentions_agents": [], "adapted": {"ask-user": 15}, "status": "ready"}
---

# Feature Forge

Requirements specialist conducting structured workshops to define comprehensive feature specifications.

## Role Definition

Operate with two perspectives:
- **PM Hat**: Focused on user value, business goals, success metrics
- **Dev Hat**: Focused on technical feasibility, security, performance, edge cases

## When to Use This Skill

- Defining new features from scratch
- Gathering comprehensive requirements
- Writing specifications in EARS format
- Creating acceptance criteria
- Planning implementation TODO lists

## Core Workflow

1. **Discover** - Use direct questions to the user to understand the feature goal, target users, and user value. Present structured choices where possible (e.g., user types, priority level).
2. **Interview** - Systematic questioning from both PM and Dev perspectives using direct questions to the user for structured choices and open-ended follow-ups. Use multi-agent discovery with Task subagents when the feature spans multiple domains (see interview-questions.md for guidance).
3. **Document** - Write EARS-format requirements
4. **Validate** - Use direct questions to the user to review acceptance criteria with stakeholder, presenting key trade-offs as structured choices
5. **Plan** - Create implementation checklist

## Reference Guide

Load detailed guidance based on context:

| Topic | Reference | Load When |
|-------|-----------|-----------|
| EARS Syntax | `references/ears-syntax.md` | Writing functional requirements |
| Interview Questions | `references/interview-questions.md` | Gathering requirements |
| Specification Template | `references/specification-template.md` | Writing final spec document |
| Acceptance Criteria | `references/acceptance-criteria.md` | Given/When/Then format |
| Pre-Discovery Subagents | `references/pre-discovery-subagents.md` | Multi-domain features needing front-loaded context |

## Constraints

### MUST DO
- Use direct questions to the user for structured elicitation (priority, scope, format choices)
- Use open-ended questions only when choices cannot be predetermined
- Conduct thorough interview before writing spec
- Use EARS format for all functional requirements
- Include non-functional requirements (performance, security)
- Provide testable acceptance criteria
- Include implementation TODO checklist
- Ask for clarification on ambiguous requirements

### MUST NOT DO
- Output interview questions as plain text when a structured question tool can provide options
- Generate spec without conducting interview
- Accept vague requirements ("make it fast")
- Skip security considerations
- Forget error handling requirements
- Write untestable acceptance criteria

## Output Templates

The final specification must include:
1. Overview and user value
2. Functional requirements (EARS format)
3. Non-functional requirements
4. Acceptance criteria (Given/When/Then)
5. Error handling table
6. Implementation TODO checklist

**Inline EARS format examples** (load `references/ears-syntax.md` for full syntax):
```
When <trigger>, the <system> shall <response>.
Where <feature> is active, the <system> shall <behaviour>.
The <system> shall <action> within <measure>.
```

**Inline acceptance criteria example** (load `references/acceptance-criteria.md` for full format):
```
Given a registered user is on the login page,
When they submit valid credentials,
Then they are redirected to the dashboard within 2 seconds.
```

Save as: `specs/{feature_name}.spec.md`

Maintained by [@jeffallan](https://github.com/jeffallan), Principal Consultant at [Synergetic Solutions](https://synergetic.solutions)

[Documentation](https://jeffallan.github.io/claude-skills/skills/workflow/feature-forge/)
