---
name: hunt
description: Finds root cause before any fix. Use when something errors, crashes, regresses, or used to work. Not for code review or new features.
when_to_use: 排查, 报错, 崩溃, 回归, 截图回归, 判断错误原因, 判断为什么报错, 反复修不好, debug, regression, used to work, broke after update, why broken, not working, what's wrong, fix error, stack trace, 以前是好的
dispatch_intent: Error, crash, regression, screenshot-reported defect, test failure, stale cache, runtime boundary, why broken
skill_id: debugging/hunt
title: Hunt
source_url: https://github.com/tw93/Waza/tree/840f926af48c98e0b36a3a64658a9ddc4cf7e716/plugins/waza/skills/hunt
license: MIT
shelf: quality
aletheore: {"id": "debugging/hunt", "shelf": "quality", "category": "debugging", "origin": "https://github.com/tw93/Waza/tree/840f926af48c98e0b36a3a64658a9ddc4cf7e716/plugins/waza/skills/hunt", "license": "MIT", "license_source": "LICENSE", "upstream_commit": "840f926af48c98e0b36a3a64658a9ddc4cf7e716", "quality": 4, "curation_note": "root cause before fix, bisect, evidence ladder; adapt: strip emoji-prefix persona and slash refs", "needs_tool": null, "vet": {"injection_pattern_hits": [], "portability_flags": ["slash"], "companion_files": 5, "has_scripts": false}, "curated_on": "2026-10-18", "requires": [], "mentions_agents": [], "adapted": {"slash-skill": 2}, "status": "ready"}
---

# Hunt: Diagnose Before You Fix

Prefix your first line with 🥷 inline, not as its own paragraph.

A patch applied to a symptom creates a new bug somewhere else.

## Outcome Contract

- Outcome: the root cause is identified before any fix is applied.
- Done when: one sentence explains the cause, every observed symptom fits it, and the fix or handoff is verified against a reproducible check.
- Evidence: source trace, repro command or UI path, logs or state, targeted test/build output, and runtime evidence for UI or native defects.
- Output: root cause, fix or handoff, verification result, and any unswept sibling risks.
- Authorization: "diagnose", "investigate", "why", "look into", "排查", "看看", or equivalent is report-only. Apply a fix only when the current request explicitly asks to fix, change, implement, or optimize, or when an authorization to do so is still in force for this same unfinished task with the action, scope, and goal unchanged; root-cause proof is still required first. A fix authorization never carries commit, push, publish, or any destructive action with it.

**Do not touch code until you can state the root cause in one sentence:**
> "I believe the root cause is [X] because [evidence]."

Name a specific file, function, line, or condition. "A state management issue" is not testable. "Stale cache in `useUser` at `src/hooks/user.ts:42` because the dependency array is missing `userId`" is testable. If you cannot be that specific, you do not have a hypothesis yet.

## Diagnosis Signals

Hypothesis quality gate: the hypothesis must explain every observable symptom, not just the one reported first; partial coverage is a symptom-level guess, not a root cause. A symptom the reporter waves off as unrelated is still a symptom the hypothesis has to cover. For timing-dependent issues (flicker, intermittent failure, race), reproduce reliably before diagnosing.

Rationalization smells: "I'll just try this" = no hypothesis, write it first. "I'm confident" = run the instrument that proves it. "Probably the same issue" = re-read the execution path from scratch. "It works on my machine" = enumerate env differences before dismissing. "One more restart" = read the last error verbatim; never restart more than twice without new evidence.

## Durable Context Preflight

See [references/durable-context.md](references/durable-context.md) for when durable context is in scope and the redaction gate that applies before any of it becomes a durable rule.

For `hunt`: durable context is hypothesis fuel only, and current code, logs, and repro evidence override memory. It never replaces a fresh root-cause sentence or a reproducible symptom list.

## Bisect Mode

Activate when: "以前是好的", "之前是好的", "used to work", "上一次提交还是对的", "broke after update", or the user remembers a specific good commit or version.

- Protect the user's worktree first: `git status --short --branch -uall`. Any modified, staged, or untracked files mean no bisect in the current checkout: run it in a temporary detached worktree and remove that worktree when done. If a temporary worktree is impossible, stop and ask for explicit cleanup/stash approval.
- If the last-good version is only a few releases back, `git diff <last-good>..HEAD -- <suspect path>` and read the delta first. The regression is usually visible there at a fraction of a bisect's cost; fall through to bisect only when the diff is too large or the culprit is not obvious.
- Bisect only with a non-interactive pass/fail command defined up front, and keep the bookkeeping in git (`git bisect good/bad`), including when you test a suspect commit directly. When it names the culprit, read only that diff down to the specific line, then run `git bisect reset` before removing the temporary worktree.

## Repeated Regression / Screenshot Reference Mode

Activate when the user says the same issue is still wrong after a fix, provides a "good" screenshot/version/file, or describes a visual result as previously correct.

Treat the reference as evidence, not decoration: list every reported and visible symptom in the user's concrete words; identify the reference oracle (last-good commit, old build, fixture, screenshot, described expected state); define the pass/fail check before editing; then name the exact current-vs-reference delta. Do not generalize a visual defect into "style polish" when the evidence points to a broken render, race, font pipeline, or state path.

If the issue is purely subjective UI taste, route to `/ui`. If it is rendering, state, timing, build output, font generation, or a regression from a known-good version, stay in `hunt`.

## Scope Blast Mode

Activate after fixing a root-cause pattern, before declaring the bug done; also when the user says "举一反三", "举一反三深入看看", or "其他地方有没有同样问题". The same shape often hides in N other places; one local fix that ignores the blast leaves N - 1 bugs in the tree.

Extract the pattern signature (the specific function, regex, API call, CSS selector, lock acquisition, validation skip, or input boundary that produced the bug) and `grep -rn` it across the repo, excluding generated dirs, build output, and vendored deps; for class-of-bug patterns ("any handler missing the lock"), grep the surrounding shape, not just the literal text. For every match, answer in writing: same bug / safe to leave (why) / unsure (ask the user). Do not silently skip a match, and do not claim "fixed" until the blast report is in the Output block. Unrelated bugs the sweep surfaces get listed, not fixed in this PR, unless the user agrees.

## Confirm or Discard

Run the one probe that would fail if the hypothesis were wrong, then read it. If the evidence contradicts the hypothesis, discard it completely and re-orient on what the probe just showed. Do not stack a fix onto a disproven hypothesis, and do not keep one just because the code "looks like" the cause.

## Runtime Evidence Ladder

Use this ladder before claiming a bug is fixed:

1. Source trace: name the exact function, state transition, file, line, or condition that can produce the symptom.
2. Deterministic repro: run or write the smallest command, fixture, UI path, or scenario that produces it.
3. Logs/state/cache: inspect the runtime state that proves the path was reached, including queues, DB rows, caches, temp files, generated outputs, or external tool logs.
4. Build/test: run the narrow test or build that exercises the fix.
5. Real runtime check: for UI, native app, browser, rendering, or visual bugs, open the app/page/artifact and verify the visible result with a screenshot or concrete checklist.

Compile-only is not enough for UI, native-app, visual, rendering, or generated-artifact bugs. If the runtime check is impossible in the environment, say why and hand off the exact screen, command, or artifact to verify.

When the reporter's environment is the missing rung and it cannot be reproduced locally, the next artifact is a read-only probe they can paste and run, not another hypothesis. Have it print the environment, the disputed measurement, and the state of whatever the hypothesis turns on, and nothing that could carry a secret or a private path. Assume none of your own layout: their install method, directory conventions, locale, shell, and version all differ, so discover rather than hardcode. Ship it as plain copyable text with one command to run and one block to paste back.

For recurring classes of failures, load `references/failure-patterns.md` before adding a second fix.

## Native App Freeze Mode

For beachballs, not responding, tab-switch freezes, first-open lag, idle wake stalls, overlay lockups, or frozen-app screenshots, load `references/logging-techniques.md` (Native App Freeze Mode) before changing code.

## Targeted Logging

Every log is a yes/no question: "if this prints X before Y, hypothesis A survives; otherwise A is dead." A log that cannot rule a hypothesis in or out is noise. Remove temporary logs before finishing; gate persistent diagnostics behind the project's debug flag. If adding a log changes the behavior, that is itself evidence of a timing, lifecycle, or concurrency problem. Full playbook: `references/logging-techniques.md`.

## Rendering Bug Mode

For PDF output, page breaks, font rendering, or print layout defects, load `references/rendering-debug.md`; it carries the activation triggers and the diagnosis checklist (WeasyPrint quirks, font loading, page overflow, browser print CSS).

## IME / Unicode Issues

For input method, character rendering, or text encoding bugs (IME state, cursor drift, emoji splitting, composition events), check `references/ime-unicode.md` first before forming a hypothesis.

## Hard Rules

- **Same symptom after a fix is a hard stop; so is "let me just try this."** Both mean the hypothesis is unfinished. Re-read the execution path from scratch before touching code again.
- **After three failed hypotheses, stop.** Use the Handoff format below to surface what was checked, what was ruled out, and what is unknown. Ask how to proceed.
- **External tool failure: diagnose before switching.** When an MCP tool or API fails, determine why first (server running? API key valid? Config correct?) before trying an alternative.
- **System/tooling symptoms need a lower-layer baseline.** Before blaming the visible app, generated file, or top-level feature, measure the raw lower layer first: OS capture versus post-processing, runtime service versus UI, compiler/toolchain versus test assertion, network/API versus client handling. Retire hypotheses that the baseline disproves instead of circling them.
- **Visual/rendering bugs: static analysis first.** Trace paint layers, stacking contexts, and layer order in DevTools before adding console.log or visual debug overlays. Logs cannot capture what the compositor does. Only add instrumentation after static analysis fails.
- **Behavioral / lifecycle / async bugs: instrument while forming the hypothesis.** Window lifecycle, event delivery, navigation, focus, timer, state-machine, and async-ordering bugs almost never yield to static reading alone. The moment the hypothesis involves "this callback fires before/after that one", "this state should be X when Y runs", or "this object should still be alive here", add the log before writing any fix; two guesses in a row is the hard-stop signal. Compositor behavior needs DevTools, not logs; pure-logic bugs (wrong formula, off-by-one) need only static analysis.
- **Tuning magic numbers past round three: stop, unify.** When a spacing / sizing / threshold value has been adjusted three times and still looks wrong, the bug is structural, not numeric. Replace the N independent values with one named token (`Spacing.s4`, `--gap-content`, etc.) and verify the asymmetry was hiding a missing constraint. Asymmetry that survives tuning is structural; more tuning will not converge.
- **Performance complaints need numbers.** For "slow", "laggy", or memory-growth reports outside Native App Freeze Mode, measure the baseline first (wall-clock time, profile sample, memory footprint), fix, then re-measure and report before/after numbers. "Feels faster" is not evidence.
- **Fix the cause within the authorized scope.** Continue necessary fixes, including a prerequisite refactor such as a shared interface change once you state why it is needed; ask only when the fix expands that scope or needs an unresolved behavior tradeoff, and file count alone is not an approval boundary. Keep unrelated refactors separate, and route an explicitly requested feature to its own workflow under the same completion ledger after diagnosis; a bug-fix authorization alone does not authorize an unrequested feature.

## Gotchas

| What happened | Rule |
|---------------|------|
| Patched the wrong copy of a duplicated surface | Trace the execution path backward to the instance that actually renders before touching any file |
| Orchestrator reported RUNNING while a downstream stage was misconfigured | In multi-stage pipelines, test each stage in isolation |
| Race condition diagnosed as a stale-state bug | For timing-sensitive issues, inspect event timestamps and ordering before state |
| Reproduced locally but failed in CI | Align the environment first (runtime version, env vars, timezone), then chase the code |
| Worked when launched from app, broke when opened via file association / drag-drop / deep link / external proxy | Reproduce using the exact entry point the user described. App-internal init differs from cold-launch-with-file init; state may not be ready when the document arrives. |
| Fix matched the reporter's setup but changed nothing for everyone else, or regressed the default | A defect report is evidence, not the full scope. State whether the fix changes the default experience for all users or only the reporter's configuration, and prefer fixing the default path. |
| Broke after toggling theme / mode / locale, fine after restart | State not re-applied on the toggle path. Trace the toggle's recompute or invalidation route first; do not tune styles pixel by pixel while the state path is broken. |
| Changed the algorithm but the output stayed wrong | The reader may be hitting persisted output written by the old code (scan results, analysis cache, snapshot with a TTL). Changing generated-then-persisted data requires invalidating or version-bumping the old cache in the same change; before re-diagnosing, confirm the runtime is not reading stale data. |
| Fixed the one cause that reproduced, shipped, and the same gate blocked the next user for a different reason | A guard that refuses has a set of causes, not one. Enumerate every branch that can refuse before shipping, and give each a distinguishable code, a one-line reason, and a next command. |
| The user's observation and the log disagreed, and the log won | Trust the observation and treat the gap as an un-instrumented path. A probe that passes on the happy path says nothing about the failing one; a probe that cannot reproduce is an invalid probe, not an absent defect. |
| Patched a capability-gated feature on a surface that never offered the capability | Confirm the run surface (simulator, device, sandbox, restricted entitlement) supports it before writing a fix. If it does not, say so and stop; no source change makes it appear. |

## Output

### Success Format

Open the wrap-up with one plain line stating the outcome and whether the changes are committed; the block below supports that line, it does not replace it.

```
Root cause:        [what was wrong, file:line]
Fix:               [what changed, file:line]
Sibling sweep:     [N same-shape sites checked, N fixed / none found / not run, why]
Confirmed:         [evidence or test that proves the fix]
Tests:             [pass/fail count, regression test location]
Regression guard:  [test file:line] or [none, reason]
```

Status: **resolved**, **resolved with caveats** (state them), or **blocked** (state what is unknown).

**Regression guard rule**: for any bug that recurred or was previously "fixed", the fix is not done until:
1. A regression test exists that fails on the unfixed code and passes on the fixed code.
2. The test lives in the project's test suite, not a temporary file.
3. The commit message states why the bug recurred and why this fix prevents it.
4. Red-green was **run**, not assumed: run the new test against the unfixed code in an isolated temporary checkout or fixture, then against the fix. Do not revert or stash the shared worktree for this comparison. A regression test that has only ever been observed passing pins nothing. State the red run in the output. Two shapes make this fail silently: a framework or syntax where a failing assertion mid-test does not fail the test, so only the last one gates (in shell suites this can hinge on the bracket form alone, with one keyword swallowed and the other caught, so confirm which by running a two-line minimal repro rather than reasoning about it); and an assertion that the wrong string is absent, which passes forever because that string was never emitted under any code version. Any negative assertion ("output must not contain X") also needs a paired positive case in the same test proving the assertion can fail at all.

### Handoff Format (after 3 failed hypotheses)

Status **blocked**, then: the symptom in one sentence; each hypothesis with its test method and why it was ruled out; evidence collected (log or stack excerpts, repro steps, versions, config, runtime); what is still unknown or missing; and next steps, naming any tool, permission, or context the user must supply.
