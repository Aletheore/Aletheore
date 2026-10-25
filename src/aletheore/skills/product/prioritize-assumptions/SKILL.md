---
name: prioritize-assumptions
description: Prioritize assumptions using an Impact × Risk matrix and suggest experiments for each. Use when triaging a list of assumptions, deciding what to test first, or applying the assumption prioritization canvas.
skill_id: product/prioritize-assumptions
title: Prioritize Assumptions
source_url: https://github.com/phuryn/pm-skills/tree/07b34f64ee828df08953f505b7f747aef85ae7c6/pm-product-discovery/skills/prioritize-assumptions
license: MIT
shelf: product-design
aletheore: {"id": "product/prioritize-assumptions", "shelf": "product-design", "category": "product", "origin": "https://github.com/phuryn/pm-skills/tree/07b34f64ee828df08953f505b7f747aef85ae7c6/pm-product-discovery/skills/prioritize-assumptions", "license": "MIT", "license_source": "LICENSE", "upstream_commit": "07b34f64ee828df08953f505b7f747aef85ae7c6", "quality": 3, "curation_note": "prioritization", "needs_tool": null, "vet": {"injection_pattern_hits": [], "portability_flags": ["claude-tools"], "companion_files": 0, "has_scripts": false}, "curated_on": "2026-10-18", "requires": [], "mentions_agents": [], "adapted": {"arguments": 1}, "status": "ready"}
---

## Prioritize Assumptions

Triage assumptions using an Impact × Risk matrix and suggest targeted experiments.

### Context

You are helping prioritize assumptions for **[user input]**.

If the user provides files with assumptions or research data, read them first.

### Domain Context

**ICE** works well for assumption prioritization: Impact (Opportunity Score × # Customers) × Confidence (1–10) × Ease (1–10). Opportunity Score = Importance × (1 − Satisfaction), normalized to 0–1 (Dan Olsen). **RICE** splits Impact into Reach × Impact separately: (R × I × C) / E. See the `prioritization-frameworks` skill for full formulas and templates.

### Instructions

The user will provide a list of assumptions to prioritize. Apply the following framework:

1. **For each assumption**, evaluate two dimensions:
   - **Impact**: The value created by validating this assumption AND the number of customers affected (in ICE: Impact = Opportunity Score × # Customers)
   - **Risk**: Defined as (1 - Confidence) × Effort

2. **Categorize each assumption** using the Impact × Risk matrix:
   - **Low Impact, Low Risk** → Defer testing until higher-priority assumptions are addressed
   - **High Impact, Low Risk** → Proceed to implementation (low risk, high reward)
   - **Low Impact, High Risk** → Reject the idea (not worth the investment)
   - **High Impact, High Risk** → Design an experiment to test it

3. **For each assumption requiring testing**, suggest an experiment that:
   - Maximizes validated learning with minimal effort
   - Measures actual behavior, not opinions
   - Has a clear success metric and threshold

4. **Present results** as a prioritized matrix or table.

Think step by step. Save as markdown if the output is substantial.

---

### Further Reading

- [Assumption Prioritization Canvas: How to Identify And Test The Right Assumptions](https://www.productcompass.pm/p/assumption-prioritization-canvas)
- [Continuous Product Discovery Masterclass (CPDM)](https://www.productcompass.pm/p/cpdm) (video course)
