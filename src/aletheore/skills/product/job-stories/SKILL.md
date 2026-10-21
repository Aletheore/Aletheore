---
name: job-stories
description: Create job stories using the 'When [situation], I want to [motivation], so I can [outcome]' format with detailed acceptance criteria. Use when writing job stories, creating JTBD-style backlog items, or expressing user situations and motivations.
skill_id: product/job-stories
title: Job Stories
source_url: https://github.com/phuryn/pm-skills/tree/07b34f64ee828df08953f505b7f747aef85ae7c6/pm-execution/skills/job-stories
license: MIT
shelf: product-design
aletheore: {"id": "product/job-stories", "shelf": "product-design", "category": "product", "origin": "https://github.com/phuryn/pm-skills/tree/07b34f64ee828df08953f505b7f747aef85ae7c6/pm-execution/skills/job-stories", "license": "MIT", "license_source": "LICENSE", "upstream_commit": "07b34f64ee828df08953f505b7f747aef85ae7c6", "quality": 3, "curation_note": "job stories", "needs_tool": null, "vet": {"injection_pattern_hits": [], "portability_flags": [], "companion_files": 0, "has_scripts": false}, "curated_on": "2026-10-18", "requires": [], "adapted": {}, "status": "ready", "mentions_agents": []}
---
# Job Stories

Create job stories using the 'When [situation], I want to [motivation], so I can [outcome]' format. Generates stories with detailed acceptance criteria focused on user situations and outcomes.

**Use when:** Writing job stories, expressing user situations and motivations, creating JTBD-style backlog items, or focusing on user context rather than roles.

**Arguments:**
- `$PRODUCT`: The product or system name
- `$FEATURE`: The new feature to break into job stories
- `$DESIGN`: Link to design files (Figma, Miro, etc.)
- `$CONTEXT`: User situations or job scenarios

## Step-by-Step Process

1. **Identify user situations** that trigger the need
2. **Define motivations** underlying the user's behavior
3. **Clarify outcomes** the user wants to achieve
4. **Apply JTBD framework:** Focus on the job, not the role
5. **Create acceptance criteria** that validate the outcome is achieved
6. **Use observable, measurable language**
7. **Link to design mockups** or prototypes
8. **Output job stories** with detailed acceptance criteria

## Story Template

**Title:** [Job outcome or result]

**Description:** When [situation], I want to [motivation], so I can [outcome].

**Design:** [Link to design files]

**Acceptance Criteria:**
1. [Situation is properly recognized]
2. [System enables the desired motivation]
3. [Progress or feedback is visible]
4. [Outcome is achieved efficiently]
5. [Edge cases are handled gracefully]
6. [Integration and notifications work]

## Example Job Story

**Title:** Track Weekly Snack Spending

**Description:** When I'm preparing my weekly allowance for snacks (situation), I want to quickly see how much I've spent so far (motivation), so I can make sure I don't run out of money before the weekend (outcome).

**Design:** [Figma link]

**Acceptance Criteria:**
1. Display Spending Summary with 'Weekly Spending Overview' section
2. Real-Time Update when expense logged
3. Progress Indicator (progress bar showing 0-100% of weekly budget)
4. Remaining Budget Highlight in prominent color
5. Detailed Spending Log with breakdown by category
6. Notifications at 80% budget threshold
7. Weekend-Specific Reminder at 90% by Thursday evening
8. Easy Access and Navigation to detailed breakdown

## Output Deliverables

- Complete set of job stories for the feature
- Each story follows the 'When...I want...so I can' format
- 6-8 acceptance criteria focused on outcomes
- Stories emphasize user situations and motivations
- Clear links to design and prototypes

---

### Further Reading

- [Jobs-to-be-Done Masterclass with Tony Ulwick and Sabeen Sattar](https://www.productcompass.pm/p/jobs-to-be-done-masterclass-with) (video course)
