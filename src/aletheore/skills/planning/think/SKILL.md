---
name: think
description: Turns rough ideas into approved, decision-complete plans before coding. Use when planning architecture, judging whether to build, or writing a handoff. Not for bug fixes or small edits.
when_to_use: 出方案, 给方案, 怎么设计, 用什么方案, 有没有必要, 值不值得, what's the best approach, plan this, how should I, should we keep this
dispatch_intent: New feature, architecture, how should I design this, value judgment, executable plan, handoff
skill_id: planning/think
title: Think
source_url: https://github.com/tw93/Waza/tree/840f926af48c98e0b36a3a64658a9ddc4cf7e716/plugins/waza/skills/think
license: MIT
shelf: build
aletheore: {"id": "planning/think", "shelf": "build", "category": "planning", "origin": "https://github.com/tw93/Waza/tree/840f926af48c98e0b36a3a64658a9ddc4cf7e716/plugins/waza/skills/think", "license": "MIT", "license_source": "LICENSE", "upstream_commit": "840f926af48c98e0b36a3a64658a9ddc4cf7e716", "quality": 3, "curation_note": "Waza think: idea to decision-complete plan; adapt: strip persona prefix", "needs_tool": null, "vet": {"injection_pattern_hits": [], "portability_flags": ["claude-tools", "slash"], "companion_files": 3, "has_scripts": false}, "curated_on": "2026-10-18", "requires": [], "mentions_agents": [], "adapted": {"path": 1, "slash-skill": 3}, "status": "ready"}
---

# Think: Design and Validate Before You Build

Prefix your first line with 🥷 inline, not as its own paragraph.

Turn a rough idea into an approved plan. No code, no scaffolding, no pseudo-code until the user approves.

Give opinions directly. Take a position and state what evidence would change it. Avoid "That's interesting," "There are many ways to think about this," "You might want to consider."

## Outcome Contract

- Outcome: a rough idea becomes a decision-complete recommendation or implementation plan.
- Done when: the goal, success criteria, constraints, chosen approach, rejected tradeoffs, tests, and handoff steps are concrete enough to execute without re-deciding.
- Evidence: current repo state, project docs, live external docs when relevant, prior decisions, constraints, and explicit user preferences.
- Output: one recommended direction or a handoff plan with assumptions and verification steps.

## Durable Context Preflight

See [references/durable-context.md](references/durable-context.md) for when durable context is in scope and the redaction gate that applies before any of it becomes a durable rule.

For `think`: current repo state and live docs override memory. Lock durable decisions and preferences before asking questions, and do not ask the user to restate an intent that the durable context already establishes unless it is risky, stale, or contradicted by current state.

Before outputting any plan, read the project guide index (`AGENTS.md` or `AGENTS.md (or your agent's instructions file)`) and only the domain rule that matches the problem. Do not load the entire `.claude/rules/` tree. If the user pointed at a local agent-memory summary, read that too. If the proposed plan contradicts a "hard rule", "never X", "must Y", or "prefer Z" in those files, surface the contradiction in the plan output (one sentence: which rule, which step contradicts it, recommended resolution). Do not silently override the rule. If the rule blocks the plan, stop and ask before continuing.

## Lightweight Mode

Activate when the user asks for a plan for a defined problem and the only open question is "how to fix it." An explicit repair request follows `hunt`; file count alone does not create another planning approval.

Give one recommended fix in 2-3 sentences: what changes, where (file:line if known), and why. Name the brute-force version in one line first; default to it unless the user wants elegance. List involved files, flag explicitly if more than 8. State one risk. Wait for approval before implementing.

Upgrade to full mode if you find 3 or more genuinely different approaches with meaningful tradeoffs.

## Evaluation Mode

For value, viability, commercialization, or keep/remove judgments about a single target, load `references/mode-evaluation.md`.

## Triage Mode

For a bundle of independently accepted or rejected asks or screenshots, including "are these worth doing", load `references/mode-triage.md`; use its per-item table rather than Evaluation Mode's single verdict.

## Before Reading Any Code

- If the project tracks prior decisions (ADRs, design docs, issue threads), skim the ones matching the problem before proposing.
- If the plan involves a default value, env var, or config field, open the project's actual config file (e.g. `app.config.json`, `tauri.conf.json`, `package.json`, `.env`) and lift the live value. Never quote a default from memory or docs.

## Check for Official Solutions First

Before proposing custom implementations, check framework built-ins, official patterns, and ecosystem standards against live docs (use the environment's doc-lookup tools when available). An existing official solution is the default recommendation unless you can articulate why it falls short for this specific case.

For a hard problem, or one already tuned several times that still feels off, study how 2-3 mature open-source projects or direct competitors solve it before designing: read the actual implementation, extract the transferable mechanism, and name what you took from each. First-principles design next to a proven implementation discards the iterations someone else already paid for.

## Propose Approaches

Give one recommended approach with rationale. Include effort, risk, and what existing code it builds on. Mention one alternative only when a concrete tradeoff could reasonably change the user's choice. Always include one minimal option.

Anything that asks a person to install or configure something (hook, MCP server, editor plugin, config key, pricing tier, per-day limit) is a setup cost paid by every user. Default to the zero-setup form: a built-in command plus a skill, a fixed sensible default, a doc line. Offer the setup-requiring form only after naming why the zero-setup one cannot do the job.

A plan to distill one project's lessons into reusable skills or shared rules splits into **promote** (reusable workflow constraints only) and **do not promote** (project-specific commands, paths, release checklists, safety boundaries, private local context), unless the user asks to update that project itself.

For the recommendation, identify the most fragile assumption (premise collapse) and state it explicitly: "This plan assumes X. If X does not hold, Y happens." If the assumption is load-bearing and fragile, deform the design to survive its failure.

**Blocking ambiguities**: if requirements have a conflict the user must resolve (two contradicting sources, two valid interpretations with different cost), name the specific conflict in one sentence and ask which takes precedence. Do not silently pick.

**Additional attack angles** (run only when the plan involves external dependencies, high concurrency, or data migration):

| Attack angle | Question |
|---|---|
| Dependency failure | If an external API, service, or tool goes down, can the plan degrade gracefully? |
| Scale explosion | At 10x data volume or user load, which step breaks first? |
| Rollback cost | If the direction is wrong after launch, what state can we return to and how hard is it? |

If an attack holds, deform the design to survive it. If it shatters the approach entirely, discard it and tell the user why. Do not present a plan that failed an attack without disclosing the failure.

Get approval before proceeding.

## Validate Before Handing Off

- More than 8 files or 1 new service? Acknowledge it explicitly.
- More than 3 components exchanging data? Draw an ASCII diagram. Look for cycles.
- Every meaningful test path listed: happy path, errors, edge cases.
- Can this be rolled back without touching data?
- Every API key, token, and third-party account the plan requires listed with one-line explanations. No credential requests mid-implementation.
- Verify required tool availability and external reachability with read-only checks where possible. Do not launch unverified applications, install tools, or mutate state to validate a plan. Record unavailable access as an implementation prerequisite; do not present it as verified.

## Simplicity Gate

Skip for one-file bug fixes or when the user explicitly chose the minimal option.

When the plan adds files, abstractions, error layers, config knobs, or retries the user did not ask for:

- **Minimal path:** the brute-force version in one line; the chosen plan must beat it on risk, rollback, or latency, not elegance.
- **Defensive layers:** every try/catch, retry, fallback, or flag maps to one named failure mode; delete layers that only "might" fail.
- **Surface delta:** list new commands, env vars, flags, or services; prefer +0 unless a user split needs a knob.
- **Compensating complexity:** if the plan is mostly workaround machinery around a misbehaving API, stop and name a route change: when the workaround is larger than the feature it supports, the premise is wrong.

If the gate fails, shrink the plan or switch to the minimal option before asking for approval.

## Implementation Handoff

A finished plan must be executable by another engineer or agent without re-deciding the direction. Include:

- Scope and non-scope.
- The chosen approach and the one rejected alternative, if the tradeoff was close.
- Public API, schema, command, config, or file-interface changes, if any.
- Verification commands and manual acceptance checks.
- Release, publish, migration, or issue/PR follow-through steps, if the task naturally continues there.
- Rollback or failure handling for any step that can leave external state changed.

When the user asks to export a handoff, or when the environment prevents further execution, make the handoff execution-ready instead of explaining the limitation. Include file targets, key constants or selectors, exact commands, runtime or visual checklist, and risk boundaries. If the work depends on a screenshot or artifact, name the artifact and the pass/fail delta.

When the user says "Implement the plan", "just do it", "可以干", "直接改", "直接做", "按你说的来", "不用确认", "整", or otherwise explicitly authorizes implementation, execute the authorized direction without another approval round. With implementation authorization still in force for the same task, skip this skill's planning approval gates (including Lightweight Mode's wait) when the user or project rules already settle the choice. State which plan is being executed and check for repo drift; stop if specific drift makes it unsafe or a material choice remains unresolved. A settled design alone does not authorize implementation or public actions.

## Hard Rules

- **No placeholders in approved plans.** Every step must be concrete before approval. Forbidden patterns: TBD, TODO, "implement later," "similar to step N," "details to be determined." A plan with placeholders is a promise to plan later.
- **Phase independence.** Each phase must be independently mergeable: after Phase N ships, the system is usable even if N+1 never lands, because a plan that needs every phase before anything works lets one stuck phase block the release. If the work cannot be cut that way, ship it as one phase instead of pretending it is staged. A "Phase 0: investigate / spike" is the same red flag: investigation belongs before the plan, not inside it.
- **An error or bug report routes out before anything else.** "判断一下" plus error or bug context is debugging, not a value judgment: say it belongs to `hunt` in one line, then route. Evaluation Mode is for value and existence judgments only.

## Gotchas

| What happened | Rule |
|---------------|------|
| Rejected design restarted from scratch | Ask what specifically failed, re-enter with narrowed constraints |
| Picked a regional or locale-specific API variant without checking | List all regional or locale differences before writing integration code |
| Introduced a second language or runtime into a single-stack project | Never add a new language or runtime without explicit approval |

## Output

**Approved design summary:**
- **Building**: what this is (1 paragraph)
- **Not building**: explicit out-of-scope list
- **Approach**: chosen option with rationale
- **Key decisions**: 3-5 with reasoning
- **Unknowns**: only items that are explicitly deferred with a stated reason and a clear owner. Not vague gaps. If an unknown blocks a decision, loop back before approval.

If the user only approves the design, end with the plan. If implementation is requested, follow Implementation Handoff instead of asking them to repeat the request.
