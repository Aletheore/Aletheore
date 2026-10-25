---
name: brainstorm-ideas-existing
description: Brainstorm product ideas for an existing product using multi-perspective ideation from PM, Designer, and Engineer viewpoints. Use when generating new feature ideas, brainstorming solutions for an identified opportunity, or ideating with a product trio.
skill_id: product/brainstorm-ideas-existing
title: Brainstorm Ideas Existing
source_url: https://github.com/phuryn/pm-skills/tree/07b34f64ee828df08953f505b7f747aef85ae7c6/pm-product-discovery/skills/brainstorm-ideas-existing
license: MIT
shelf: product-design
aletheore: {"id": "product/brainstorm-ideas-existing", "shelf": "product-design", "category": "product", "origin": "https://github.com/phuryn/pm-skills/tree/07b34f64ee828df08953f505b7f747aef85ae7c6/pm-product-discovery/skills/brainstorm-ideas-existing", "license": "MIT", "license_source": "LICENSE", "upstream_commit": "07b34f64ee828df08953f505b7f747aef85ae7c6", "quality": 3, "curation_note": "discovery and experiments", "needs_tool": null, "vet": {"injection_pattern_hits": [], "portability_flags": ["claude-tools"], "companion_files": 0, "has_scripts": false}, "curated_on": "2026-10-18", "requires": [], "mentions_agents": [], "adapted": {"arguments": 1}, "status": "ready"}
---

## Brainstorm Product Ideas (Existing Product)

Multi-perspective ideation for continuous product discovery. Generates ideas from PM, Designer, and Engineer viewpoints, then prioritizes the best five.

### Context

You are supporting a product trio performing continuous product discovery for **[user input]**.

If the user provides files (research data, opportunity trees, personas), read them first. If they mention a product URL, use web search to understand the product.

### Domain Context

**Product Trio** (Teresa Torres, *Continuous Discovery Habits*): PM + Designer + Engineer collaborate on discovery together. "Best ideas often come from engineers." Discovery is not linear — loop back if experiments fail. Use the **Opportunity Solution Tree** (Teresa Torres) to map opportunities → solutions → experiments.

### Instructions

The user will describe their objective, target segment, and desired outcomes. Work through these steps:

1. **Understand the opportunity**: Confirm the product, objective, market segment, and desired outcomes. Ask for clarification if anything is ambiguous.

2. **Ideate from three perspectives** — generate 5 ideas each from:
   - **Product Manager**: Focus on business value, strategic alignment, and customer impact
   - **Product Designer**: Focus on user experience, usability, and delight
   - **Software Engineer**: Focus on technical possibilities, data leverage, and scalable solutions

3. **Prioritize the top 5 ideas** across all perspectives based on:
   - Strategic alignment with the stated objective
   - Potential impact on desired outcomes
   - Feasibility and effort required
   - Differentiation from existing solutions

4. **For each prioritized idea**, provide:
   - A clear name and one-sentence description
   - Why it was selected (reasoning)
   - Key assumptions to validate

Think step by step. Present ideas in a clear, structured format.

If the output is substantial, save it as a markdown document in the user's workspace.

---

### Further Reading

- [What Is Product Discovery? The Ultimate Guide Step-by-Step](https://www.productcompass.pm/p/what-exactly-is-product-discovery)
- [Product Trio: Beyond the Obvious](https://www.productcompass.pm/p/product-trio)
- [The Extended Opportunity Solution Tree](https://www.productcompass.pm/p/the-extended-opportunity-solution-tree)
- [Product Model First Principles: Product Discovery, Product Delivery, and Product Culture In Depth](https://www.productcompass.pm/p/product-model-first-principles-discovery-deliver)
- [Continuous Product Discovery Masterclass (CPDM)](https://www.productcompass.pm/p/cpdm) (video course)
