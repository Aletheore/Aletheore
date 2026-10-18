# Failure Pattern Reference

Use this when a bug has repeated, a first fix did not hold, or the symptom smells like runtime state rather than local code syntax.

## Stale Verifier Or Tool Cache

Signals: verifier output points at deleted temp worktrees, old generated files, or paths outside the current repo; rerunning after a clean checkout changes the file path but not the current code.

Checks:
- Confirm the reported path exists.
- Clear the tool cache only after proving the path is stale.
- Re-run the same verifier from the current repo root.

## Worker Queue Or DB Boundary

Signals: UI says work is running but no worker processes it; logs show scheduler activity but no queued row; retry fixes one item but not the pipeline.

Checks:
- Trace request -> enqueue -> worker pickup -> persistence -> UI refresh.
- Inspect queue rows or job state directly.
- Add a regression test around the enqueue boundary, not only the worker body.

## Generated Rebuild Boundary

Signals: source changed but generated output, app bundle, CLI artifact, archive, checksum, or release package still contains old behavior.

Checks:
- Identify the source-to-artifact rule.
- Verify the build system watches the source path.
- Inspect the generated artifact contents, not just the source diff.

## Guard Lifetime Race

Signals: permission, auth, or state guard looks correct locally but a delayed callback, app relaunch, or alternate entry point bypasses it.

Checks:
- Trace guard creation, retention, invalidation, and every alternate entry point.
- Verify cold launch, warm launch, deep link/file open, and retry paths when applicable.
- Prefer explicit durable state over transient flags when the guard must survive relaunch.

## Atomic Temp Filename

Signals: concurrent runs collide, cleanup removes the wrong file, or a partially written output is observed.

Checks:
- Use unique temp directories or atomic rename.
- Keep cleanup scoped to files created by the current run.
- Test two concurrent or back-to-back runs when the tool supports it.

## Path, Cwd, Or Symlink Escape

Signals: an operation intended for one root touches a sibling directory, follows a symlink unexpectedly, or behaves differently from another working directory.

Checks:
- Resolve and compare canonical roots before writing or deleting.
- Reject paths outside the allowed root after symlink resolution.
- Reproduce from a non-default cwd and through any UI entry point that supplies paths.

## CLI Effect Scope Drift

Signals: preview, dry-run, size, count, or report output is computed from one predicate, but execution mutates a broader or different set.

Checks:
- Trace display, dry-run, and mutation predicates to the same source of truth.
- Compare planned paths or records with executor input in a regression test.
- Assert partial failures report the exact skipped and completed items.

## CLI Wrapper Or PATH Drift

Signals: source-tree invocation works, but the installed command, package wrapper, PATH shim, completion, or package-manager install path runs old code or a different binary.

Checks:
- Inspect built package contents, shebang, executable bit, and wrapper target.
- Reproduce through a temp prefix or package-manager install path, not only from source.
- Check PATH order and use absolute system-tool paths where wrappers should not intercept.

## Interactive Stdin Or TTY Hang

Signals: CI stalls, spinner never finishes, a subprocess reads from the script body, or an auth prompt appears in non-interactive mode.

Checks:
- Reproduce with stdin redirected and with TTY/non-TTY paths separated.
- Add test-mode or no-auth guards around real prompts and system changes.
- Stub external prompt tools through PATH when timeout wrappers exec real binaries.

## Subprocess Pipe Backpressure

Signals: a long-running child process hangs only on large output, small fixtures pass, or the parent waits for exit before reading stdout/stderr. The child may be blocked on a full pipe buffer while the parent is blocked on `wait`.

Checks:
- Drain stdout and stderr while the process runs, or explicitly inherit/redirect streams when output is not needed.
- Test with output larger than a typical pipe buffer, not only tiny fixtures.
- Preserve stderr tails or structured error output for diagnostics without holding the whole stream in memory.

## Signal Or Partial-Failure Mapping

Signals: cancel, timeout, SIGINT, or SIGTERM is reported as success or as a normal business failure; temp files, locks, or operation logs make retries look complete.

Checks:
- Classify interrupted execution separately from success and expected validation failures.
- Assert temp cleanup, lock release, and operation-log state after interruption.
- Test retry and idempotency after a partial write.

## CLI Stream Contract Regression

Signals: automation breaks after human logs, progress output, JSON shape, stdout/stderr routing, or exit-code behavior changes.

Checks:
- Assert exit code, stdout, and stderr separately in CLI tests.
- Keep human diagnostics off stdout for machine-readable modes.
- Snapshot or parse JSON/schema output and include non-interactive coverage.

## Snapshot Rebuild Drops Carried Field

Signals: live data shows up at the data source and on the wire but a downstream view sees it empty; the field has a default value (`var x: [T] = []`, `var y: Int? = nil`) that lets memberwise init compile without it; the symptom appears only on the path where the snapshot is rebuilt (icon resolution, decoration, redaction), not on a fresh fetch.

Checks:
- Trace whether every code path that constructs the snapshot type passes the field. The Swift compiler does not warn on default-value omission in memberwise init.
- Add a unit test that fetches the snapshot, runs the rebuild path, and asserts the carried field equals the input.
- Prefer `with(...)` mutating helpers or `inout` mutation over fresh memberwise init when only one field is changing.

## Multi-Sample Command Cold Start

Signals: a CLI tool that takes `-l N` / `--samples N` / `--repeat N` returns one block of zeros and one block of real data; aggregating all blocks yields zeros; only the second sample carries real measurements.

Checks:
- Read the tool's man page for cold-start semantics. `top -l 2`, `iostat -d 2`, `vm_stat 1 2`, etc. all share this shape.
- Slice the output to the latest sample (`.suffix(perSampleSize)` on parsed lines, or look for the second instance of the header row).
- When in doubt, raise `-l` to 3 and confirm sample 2 and 3 agree; sample 1 stays zero.

## Locale-Dependent Subprocess Output

Signals: numbers parse correctly for the author and come back zero, truncated, or wildly wrong for some users; a percentage, size, or duration is right in one region and broken in another; the same parser was already patched once for a different field.

Checks:
- Force a fixed locale on every subprocess whose output is parsed (`LC_ALL=C` or the platform equivalent) rather than repairing each parser for comma decimal separators, digit grouping, or translated field labels.
- Fix this at the spawn boundary, not per call site. This shape arrives as three or four separate reports (one metric, then another, then a rendered summary) and each pointwise patch hides how many parsers are still exposed.
- Treat translated output as a format change, not a string change: field order, units, and label names can all move.

## Single-Probe Existence Check

Signals: a "is it installed / running / registered / active" verdict is wrong for a subset of users, and the wrong verdict then drives a destructive or user-visible action (flagged as orphaned, offered for deletion, a feature silently disabled). The subset shares an install method, a packaging convention, or an OS feature the probe does not know about.

Checks:
- List every legitimate way the subject can exist, then confirm the probe sees all of them. One index query, one PATH lookup, one process name, or one interface-name prefix is a partial view: system indexes can be disabled or skip a packaging convention, nested or embedded components are not registered where top-level ones are, and OS-owned interfaces borrow the naming a third-party feature also uses.
- Distinguish "probe timed out" from "subject absent". A slow index is unknown, not proof of absence; a timed-out fast path must fall through to a direct check, never to a negative verdict.
- Weight the failure asymmetrically: when the verdict authorizes removal, a false "absent" destroys data while a false "present" only leaves something behind. Require corroboration from a second source before the destructive branch.

## Aggregation Key Variant

Signals: a count, log roll-up, event tally, or per-category breakdown is short by some entries; the missing items share a trait (a system-derived path, a localized string, a prefixed command name); the base-form key matches but a derived variant (`<base>-system`, a suffix, a prefix) is silently dropped.

Checks:
- Before adding a category, grep every write site that produces this class of key and enumerate the real variants, not just the base form.
- Match with `hasPrefix` / a regex / an explicit variant list rather than exact equality on the base key.
- Add a fixture row for each known variant so a future key shape that escapes the matcher fails the test instead of the aggregate.

## Whole-Buffer Decode Collapse

Signals: a parser that works on your machine returns nothing on someone else's; the affected user has an accented device name, a non-ASCII filename, or an unusual process argument; the failure is total (every row gone) rather than partial (one row garbled). Distinct from pipe backpressure: the bytes arrived, the decode threw them away.

Checks:
- Find every strict decode of bytes produced by a child process, a device, or the filesystem (`String(data:encoding:)`, `from_utf8`, `decode('utf-8')` with no `errors=`). One invalid byte nils the entire buffer, and callers that coalesce nil to empty turn it into "the command produced nothing".
- Decode leniently wherever the bytes are a report to parse; keep the strict decode only where the bytes are a signature or checksum being verified, and fail closed there. Both stances belong in one codebase for different call sites.
- Check what the empty result means downstream. A safety guard that reads an empty process list as "nothing is running" fails open, which is the dangerous direction on a destructive path.
- Real failure is reported by exit status and timeout, not by decode success. Point callers at those instead.

## Denied Read Returns A Plausible Value

Signals: a metric is right for some subjects and wrong for others, and the split follows ownership: your own processes/files are correct, root-owned or other-user ones read zero, stale, or absent. No error is logged because the API answered.

Checks:
- Measure the boundary instead of reading the docs: run the call across owned and non-owned subjects and count how many succeed in each group. Those two counts are the evidence; a guess is not.
- Check whether the fallback for a denied read carries the same meaning as the primary source. Two different meanings in one column is the bug, even when each value is individually defensible.
- Prefer a source that answers uniformly for every subject (a tool that reports all processes regardless of owner) over a precise one that silently degrades for some.

## Recovery Gated On The Artifact It Restores

Signals: a repair, reinstall, or self-heal path reports the same dead end no matter how many times it runs; the broken state persists across reinstalls; the repair "has always been there" but no evidence exists that it ever succeeded.

Checks:
- Read the repair path's own precondition and ask whether it is true in the broken state it exists to fix. A recovery gated on the file that is missing can never fire.
- Verify every absolute tool path in a repair command actually exists on the platform. A wrong path exits non-zero, an `&&` chain silently stops, and the repair no-ops on every machine forever. Add a test that walks each path in the command and asserts it is executable.
- Do not let a test assert the source shape of the repair command; that pins the broken form as correct. Assert the observable end state instead (the service is registered, the file exists, the probe answers).
- Make the repair own the outcome rather than assume it: write the artifact, then prove it by querying the system, and log the query's raw output instead of discarding it.

## Watchdog Tuned To The Fast Path

Signals: an operation is reported as failed, stalled, or "no progress" while it was actually healthy; the report comes from users on slow links, cold caches, network volumes, or large payloads; retrying makes it fail at the same elapsed time every run.

Checks:
- For each timeout constant, name the slowest *healthy* case (a several-hundred-MB download on a slow link, a first-of-day index rebuild, a tool rebuilding its cache after a cleanup) and confirm the constant clears it with margin. This is the inverse of magic-wait coupling: there the timer is too loose to be a real signal, here it is too tight to allow a healthy slow case.
- A detector threshold has the same two directions as a timeout. Take the measurement from the incident that motivated the check and set the threshold below it; a rounder number chosen by taste excludes the one case the check exists for. Then confirm it against the healthy population so it does not fire on everything, and check what the detector prints when nothing crosses the line, since a floor that reports "nothing found" exactly when a name was needed is the same defect facing the other way.
- Replace "no output for N seconds" with a real liveness probe (a growing temp file, a byte counter, a heartbeat) and keep the timeout as the genuine stall guard.
- For each watchdog, enumerate every exit from the region it guards, including thrown errors and forks into an alternate path. A watchdog that survives a fork fires in the middle of the path that replaced it.
- Check whether a second bound already covers a genuinely hung run. If so, the extra timer can only ever fire early.

## Display-String Comparison

Signals: a comparison based on user-facing text produces a verdict that never resolves: a perpetual "update available" that installs nothing, a diff that always reports changed, a match that never fires. The two sides format the same underlying value differently.

Checks:
- Ask whether the compared value's *format* is part of a contract or something the producer restyles at will. Version display strings, filenames derived from a URL tail, and localized labels are all free-form.
- Find the machine-facing identity the platform intends for ordering or equality (a build number, a content hash, an id) and compare that, falling back to the display form only when the identity is absent on either side.
- When the fallback must stay, suppress the verdict where both strings carry the identical token sequence in a different arrangement; no genuinely newer or changed value can satisfy that.
- Fix every channel that repeats the comparison, not the one that produced the report. This shape is almost always duplicated.
