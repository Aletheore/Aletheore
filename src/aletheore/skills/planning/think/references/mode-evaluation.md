# Evaluation Mode

Activate when the user wants to judge whether something should exist, be kept, exposed, or removed. Typical triggers: "判断一下", "有没有必要", "值不值得", "should we keep this", "is this worth it", "我不想做", "商业前景", "有没有必要继续".

State the evaluation target and what kind of judgment is needed (value, risk, or tradeoff). Take a current-state snapshot: what it does, who uses it, what depends on it; grep and read before opining.

Inventory the durable entity delta before a **Keep** or **Pivot** verdict: settings, flags, environment variables, commands, services, tabs, routes, schemas, dependencies, public APIs, and long-lived helpers. Each addition must name its distinct user need, owner, maintenance and rollback cost, and why changing an existing default or affordance cannot achieve the same result. If that case is weak, remove the entity from the proposal; technical feasibility is not necessity.

For product pivot, commercialization, or business-direction requests, frame the market, user, distribution, willingness-to-pay, and maintenance burden before proposing technology. Do not assume open source, do not assume implementation comes first, and do not hide a business judgment inside a technical plan.

**Commercial readiness gate.** When the judgment is whether a product, paid feature, launch, or version is chargeable, evaluate chargeability before implementation. Check delivery and update path, first-run activation/onboarding, payment/license/trial boundary, privacy and network promises, headline-feature reliability and honest degradation, support/refund triggers, competitor wedge, and solo-maintainer maintenance burden. A product is not ready to charge because the happy path works locally; missing distribution, update, licensing, privacy disclosure, or headline-feature reliability is a Keep-building/Pivot blocker.

**Output format (Kill/Keep/Pivot):**

Line 1: one of **Kill** / **Keep** / **Pivot** as the verdict. No preamble.

Then three reasons, based on the user's actual constraints (time, motivation, business model, maintenance cost). Not generic tradeoffs.

Then state `Entity delta: +N / -N` and name any added public surface. `+0` is the preferred outcome when an existing default or path can carry the value.

If verdict is **Pivot**: list specific directions on separate lines, one per line, each actionable.

If verdict is **Kill** or major rework: list impact scope (files, dependents, migration cost) before asking for confirmation.

Do not use a build-plan template here. Do not list options. Give one verdict.

Distinction from Lightweight Mode: Lightweight answers "how to fix it" (method). Evaluation answers "should it exist" (value judgment).
