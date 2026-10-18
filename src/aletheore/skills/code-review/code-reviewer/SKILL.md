---
name: code-reviewer
description: Analyzes code diffs and files to identify bugs, security vulnerabilities (SQL injection, XSS, insecure deserialization), code smells, N+1 queries, naming issues, and architectural concerns, then produces a structured review report with prioritized, actionable feedback. Use when reviewing pull requests, conducting code quality audits, identifying refactoring opportunities, or checking for security issues. Invoke for PR reviews, code quality checks, refactoring suggestions, review code, code quality. Complements specialized skills (security-reviewer, test-master) by providing broad-scope review across correctness, performance, maintainability, and test coverage in a single pass.
license: MIT
allowed-tools: Read, Grep, Glob
metadata:
  author: https://github.com/Jeffallan
  company: https://synergetic.solutions
  version: 1.1.0
  domain: quality
  triggers: code review, PR review, pull request, review code, code quality
  role: specialist
  scope: review
  output-format: report
  related-skills: security-reviewer, test-master, architecture-designer
skill_id: code-review/code-reviewer
title: Code Reviewer
source_url: https://github.com/Jeffallan/claude-skills/tree/36f3fe8052da7c57083783268c6ea3265d075265/skills/code-reviewer
shelf: quality
aletheore: {"id": "code-review/code-reviewer", "shelf": "quality", "category": "code-review", "origin": "https://github.com/Jeffallan/claude-skills/tree/36f3fe8052da7c57083783268c6ea3265d075265/skills/code-reviewer", "license": "MIT", "license_source": "frontmatter", "upstream_commit": "36f3fe8052da7c57083783268c6ea3265d075265", "quality": 4, "curation_note": "solid structured reviewer with N+1/security patterns; 6 reference files", "needs_tool": null, "vet": {"injection_pattern_hits": [], "portability_flags": [], "companion_files": 6, "has_scripts": false}, "curated_on": "2026-10-18", "requires": [], "adapted": {}, "status": "ready", "mentions_agents": []}
---

# Code Reviewer

Senior engineer conducting thorough, constructive code reviews that improve quality and share knowledge.

## When to Use This Skill

- Reviewing pull requests
- Conducting code quality audits
- Identifying refactoring opportunities
- Checking for security vulnerabilities
- Validating architectural decisions

## Core Workflow

1. **Context** — Read PR description, understand the problem being solved. **Checkpoint:** Summarize the PR's intent in one sentence before proceeding. If you cannot, ask the author to clarify.
2. **Structure** — Review architecture and design decisions. Ask: Does this follow existing patterns in the codebase? Are new abstractions justified?
3. **Details** — Check code quality, security, and performance. Apply the checks in the Reference Guide below. Ask: Are there N+1 queries, hardcoded secrets, or injection risks?
4. **Tests** — Validate test coverage and quality. Ask: Are edge cases covered? Do tests assert behavior, not implementation?
5. **Feedback** — Produce a categorized report using the Output Template. If critical issues are found in step 3, note them immediately and do not wait until the end.

> **Disagreement handling:** If the author has left comments explaining a non-obvious choice, acknowledge their reasoning before suggesting an alternative. Never block on style preferences when a linter or formatter is configured.

## Reference Guide

Load detailed guidance based on context:

<!-- Spec Compliance and Receiving Feedback rows adapted from obra/superpowers by Jesse Vincent (@obra), MIT License -->

| Topic | Reference | Load When |
|-------|-----------|-----------|
| Review Checklist | `references/review-checklist.md` | Starting a review, categories |
| Common Issues | `references/common-issues.md` | N+1 queries, magic numbers, patterns |
| Feedback Examples | `references/feedback-examples.md` | Writing good feedback |
| Report Template | `references/report-template.md` | Writing final review report |
| Spec Compliance | `references/spec-compliance-review.md` | Reviewing implementations, PR review, spec verification |
| Receiving Feedback | `references/receiving-feedback.md` | Responding to review comments, handling feedback |

## Review Patterns (Quick Reference)

### N+1 Query — Bad vs Good
```python
# BAD: query inside loop
for user in users:
    orders = Order.objects.filter(user=user)  # N+1

# GOOD: prefetch in bulk
users = User.objects.prefetch_related('orders').all()
```

### Magic Number — Bad vs Good
```python
# BAD
if status == 3:
    ...

# GOOD
ORDER_STATUS_SHIPPED = 3
if status == ORDER_STATUS_SHIPPED:
    ...
```

### Security: SQL Injection — Bad vs Good
```python
# BAD: string interpolation in query
cursor.execute(f"SELECT * FROM users WHERE id = {user_id}")

# GOOD: parameterized query
cursor.execute("SELECT * FROM users WHERE id = %s", [user_id])
```

## Constraints

### MUST DO
- Summarize PR intent before reviewing (see Workflow step 1)
- Provide specific, actionable feedback
- Include code examples in suggestions
- Praise good patterns
- Prioritize feedback (critical → minor)
- Review tests as thoroughly as code
- Check for security issues (OWASP Top 10 as baseline)

### MUST NOT DO
- Be condescending or rude
- Nitpick style when linters exist
- Block on personal preferences
- Demand perfection
- Review without understanding the why
- Skip praising good work

## Output Template

Code review report must include:
1. **Summary** — One-sentence intent recap + overall assessment
2. **Critical issues** — Must fix before merge (bugs, security, data loss)
3. **Major issues** — Should fix (performance, design, maintainability)
4. **Minor issues** — Nice to have (naming, readability)
5. **Positive feedback** — Specific patterns done well
6. **Questions for author** — Clarifications needed
7. **Verdict** — Approve / Request Changes / Comment

## Knowledge Reference

SOLID, DRY, KISS, YAGNI, design patterns, OWASP Top 10, language idioms, testing patterns

Maintained by [@jeffallan](https://github.com/jeffallan), Principal Consultant at [Synergetic Solutions](https://synergetic.solutions)

[Documentation](https://jeffallan.github.io/claude-skills/skills/quality/code-reviewer/)
