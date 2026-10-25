# Triage Mode

Activate when the user forwards a bundle of asks: an issue with multiple requests, a batch of screenshots, a user saying "看看这几个需求", or any input containing 3+ distinct items that could each be accepted or rejected independently.

Do not treat the bundle as a to-do list. Classify each item first:

| Bucket | Meaning | Action |
|--------|---------|--------|
| **Bug** | Broken behavior with evidence | Fix |
| **Already works** | The feature exists but the reporter missed it | Point to the existing affordance |
| **Accepted improvement** | Genuine gap, low-risk, aligns with product direction | Implement |
| **Cosmetic / preference** | Subjective, no functional impact | Note it, do not implement unless the maintainer agrees |
| **Out of scope** | Conflicts with product boundary or adds unjustified complexity | Decline with one sentence |

Output the classification table first. Implement the subset already covered by explicit authorization; a read-only triage request needs approval before implementation. Ask only for an unresolved choice or scope expansion. "Already works" misidentified as missing is the most common waste; grep for the existing affordance before classifying an item as a gap.

**Negative-user feedback is not automatic scope.** Refund, churn, and "competitor X is more intuitive" complaints often land on deliberate product differentiation, not an oversight. Before converting the complaint into a rework plan, read the project's own docs for the criticized behavior named as a deliberate choice; if it is, the verdict is **Keep**, with one sentence on why the differentiation matters and a note that the maintainer can override. Do not write a "fix the friction" plan that quietly removes the differentiator.
