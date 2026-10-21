# Logging Techniques for Debugging

Every log answers a yes/no question about a hypothesis: "if this prints X before Y, hypothesis A holds; otherwise A is dead." A log that cannot rule a hypothesis in or out is noise.

## Discriminating Content

Log what discriminates between hypotheses: ordering (sequence number or timestamp), input identity key, branch taken, old-vs-new state transition, and error code plus context. Place logs at boundaries where behavior should be predictable (handler entry/exit, cache hit or miss with key, state setter with old value and caller, async callback entry, external API result) rather than in tight-loop interiors. Never log credentials, PII, or full request/response bodies.

For race conditions, flicker, or intermittent failures, also capture event identity, monotonic ordering, start and end (not just "it ran"), and thread/task/queue identity. If adding a log changes the behavior, that is evidence of a timing, lifecycle, or concurrency problem, not "logging side effects" to dismiss.

## Runner-Only Failures

When a script fails only under a specific runner (make target, CI job, test harness, cron) but passes standalone, do not edit the script with debug hacks you might forget to remove. Inject tracing from the outside via the environment the runner already passes through:

```bash
# xtrace-env.sh: sourced by every non-interactive bash via BASH_ENV
exec 19>>/path/to/persistent/xtrace.log
export BASH_XTRACEFD=19
export PS4='+ [$0:$LINENO] '
set -x
```

Run the failing pipeline as `BASH_ENV=/path/to/xtrace-env.sh make test` (or the runner's equivalent). Every bash the runner spawns appends `file:line`-stamped traces to one persistent file, surviving the runner's temp-dir cleanup, so the exact dying line is on record even when the failure needs the full pipeline to reproduce. Guard the injection with a sentinel variable if nested shells would re-source it, and delete the env file when done.

## Native App Freeze Mode

Activate when a desktop or mobile native app reports beachball, not responding, tab-switch freeze, first-open lag, idle wake stall, overlay lockup, or a screenshot shows a frozen app.

Evidence to collect before changing code:

1. Exact user path and version: first launch versus warm launch, the tab or window transition, idle duration, permissions, display count, and any setting that makes the freeze disappear.
2. Runtime capture while frozen: `sample <process>`, recent app logs, CPU and memory footprint, thread count, and whether the main thread is blocked, spinning, or allocating.
3. First-frame surface: view body work, first `.task`, synchronous icon or metadata lookup, filesystem scans, URL parent walks, notification callbacks, and app/window wake handlers.
4. Blast search after the fix: grep the same API shape across the repo, especially path parent walks, synchronous icon loading, metadata reads in render paths, and callbacks that run on the main thread.

Common native freeze traps:

- Launch, terminate, permission, audio, display, or workspace notifications doing path walks, icon lookup, filesystem scans, or process enumeration on the main thread.
- First paint hydrating a full app list, directory tree, media thumbnail set, or system status table before showing an interactive shell.
- An input-lock or full-screen overlay without a guaranteed teardown path for Escape, app deactivation, permission denial, process termination, and window close.
- Timer or sampler work that survives hidden windows, long idle periods, sleep/wake, or app reactivation.

Compile-only and source-only checks are insufficient for this mode. The outcome must include the runtime capture, the root-cause frame or state transition, the focused regression guard, and any sibling matches that were fixed or explicitly left safe.
