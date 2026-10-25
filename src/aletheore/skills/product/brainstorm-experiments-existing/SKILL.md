---
name: brainstorm-experiments-existing
description: Design experiments to test assumptions for an existing product — prototypes, A/B tests, spikes, and other low-effort validation methods. Use when validating assumptions, testing feature ideas cheaply, or planning product experiments.
skill_id: product/brainstorm-experiments-existing
title: Brainstorm Experiments Existing
source_url: https://github.com/phuryn/pm-skills/tree/07b34f64ee828df08953f505b7f747aef85ae7c6/pm-product-discovery/skills/brainstorm-experiments-existing
license: MIT
shelf: product-design
aletheore: {"id": "product/brainstorm-experiments-existing", "shelf": "product-design", "category": "product", "origin": "https://github.com/phuryn/pm-skills/tree/07b34f64ee828df08953f505b7f747aef85ae7c6/pm-product-discovery/skills/brainstorm-experiments-existing", "license": "MIT", "license_source": "LICENSE", "upstream_commit": "07b34f64ee828df08953f505b7f747aef85ae7c6", "quality": 3, "curation_note": "discovery and experiments", "needs_tool": null, "vet": {"injection_pattern_hits": [], "portability_flags": ["claude-tools"], "companion_files": 0, "has_scripts": false}, "curated_on": "2026-10-18", "requires": [], "mentions_agents": [], "adapted": {"arguments": 1}, "status": "ready"}
---

## Design Experiments (Existing Product)

Design low-effort experiments to test product assumptions before committing to full implementation.

### Context

You are helping a product team design experiments for **[user input]**. The team has a feature idea and assumptions that need validation.

If the user provides files (PRDs, assumption lists, designs), read them first.

### Instructions

The user will describe their idea and assumptions. Work through these steps:

1. **Clarify the idea and assumptions**: Confirm what the team wants to build and what they need to validate.

2. **Suggest experiments** for each assumption. Consider methods like:
   - First-click testing or task completion with a prototype
   - Feature stubs or fake door tests
   - Technical spikes
   - A/B tests on production (with risk mitigation)
   - Wizard of Oz approaches
   - Survey-based validation (behavioral, not opinion-based)

3. **Key principles to follow**:
   - Measure actual behavior, not users' opinions
   - Test responsibly — don't put users or the business at risk
   - For production tests (e.g., A/B tests), explain risk mitigation strategies
   - Aim for maximum validated learning with minimal effort

4. **For each experiment**, specify:
   - **Assumption**: What do we believe?
   - **Experiment**: What exactly will we do to validate it?
   - **Metric**: What will be measured?
   - **Success threshold**: The expected value if we are right

Think step by step. Present experiments in a clear table or structured format. Save as markdown if substantial.

---

### Further Reading

- [Testing Product Ideas: The Ultimate Validation Experiments Library](https://www.productcompass.pm/p/the-ultimate-experiments-library)
- [Assumption Prioritization Canvas: How to Identify And Test The Right Assumptions](https://www.productcompass.pm/p/assumption-prioritization-canvas)
- [What Is Product Discovery? The Ultimate Guide Step-by-Step](https://www.productcompass.pm/p/what-exactly-is-product-discovery)
- [Continuous Product Discovery Masterclass (CPDM)](https://www.productcompass.pm/p/cpdm) (video course)
