# Conditional Review Patterns

Load only the sections whose trigger appears in the diff. These are hard stops when the described failure can reach users; otherwise report them as advisory.

## Recurring or hard-to-observe bugs

For a recurring visual, layout, timing, or stateful-UI bug, do not accept another tuned constant as durable coverage. Pull the decision into a pure function and test the violated invariant, such as nonzero width, half-open hit regions, or bounded offsets. Runtime inspection proves one instance; the invariant prevents recurrence.

Do not demand a fake seam. If a shallow helper cannot exercise the real failure at its call site, report `no correct test seam` as the architectural defect. A pure function covers one wrong decision. It does not cover future callers bypassing a guard. For a silent, costly primitive such as direct deletion, raw privilege escalation, or an unbounded external command, add a source-invariant test that enumerates call sites and rejects raw usage outside an explicit allowlist.

## Captured output and asynchronous completion

When code branches on an error message or captured command output, probe what the string contains at runtime. A subprocess using inherited stdio may show diagnostics in the terminal while `error.message` contains only the command line. Prefer structured facts such as exit codes or known targets over reparsing prose.

Flag fixed `sleep`, `asyncAfter`, `setTimeout`, frame counts, or guessed timeouts that stand in for an observable completion signal. They vary across CPU speed, display refresh rate, and networks. Drive the next step from callbacks, navigation completion, frame changes, state flags, or wall-clock state as appropriate.

## Simplification and deletion

For prose, rule, skill, or guidance consolidation, read the deletions back and classify every removed behavior as `folded into X`, `redundant with Y`, or `behavior removed`. List behavior-removed entries explicitly. Deletion volume is not evidence of a good pass.

For dead-code or YAGNI claims, search the whole repository: entrypoints, docs, tests, generated dispatch tables, scripts, CI, packaging allowlists, manifests, and dynamic lookup patterns. Separate test-only from production references and chase data written but only read indirectly. If a dev tool is merely exposed by the wrong package or mirror, tighten distribution rather than deleting the tool. Partial search scope cannot justify deletion.

## History-sensitive normalization

When a diff restores a recently removed symbol, string, asset, enum case, localization entry, or config field, confirm current main still consumes it. A parity test or stale rule is not proof of life.

Before making an outlier match its siblings, inspect the change or comment that introduced the divergence. The asymmetry may deliberately avoid a known defect; normalization must preserve that protection.

## Non-atomic replacement of user files

When a diff writes to a path the user already has (`curl -o`, `>`, `tee`, open-truncate-write), ask what survives a failure partway through. Truncating the destination first means a dropped connection, timeout, or non-zero exit leaves a corrupt file and no original. Require staging into a sibling temp file, swapped in only once the content is complete.

Staging covers the paths the code tests for. It does not cover signals: with no trap, an interrupt mid-write can both strand the temp file and let the shell run past the interrupt to install partial content. A fetch running with `-fsSL`, `2>/dev/null`, or a swallowed exit code compounds this by telling the user nothing about what broke or what was left intact.

## Destructive matcher breadth

For recursion, mass deletion, traversal, ID-prefix wildcards, or fallback regex branches feeding a destructive sink, inspect:

- matcher breadth in every primary and fallback branch;
- protected-path coverage at the new entry point;
- user-confirmation paths; and
- whether the guard lives inside the deletion primitive rather than only at one caller.

Ask for the narrowest evidence authorizing deletion. Exact identifiers and exact paths can pass. Display names, vendor prefixes, common tokens, and user labels cannot safely authorize neighboring deletion.

## Duplicated derivations

Flag a classification, ordering, threshold, count, or eligibility rule computed independently in two places. Summary/list, preview/executor, score/explanation, and ordering/control pairs drift after the first one-sided change. Require one constant or pure function and have both consumers use it. When one side changes, search for its sibling.

## Test surface fidelity

A test is not coverage when it pins a helper that production never reaches or asserts the literal source form of a command/config string instead of the shipped entry point. Ask whether it fails on the unfixed code and whether users execute the asserted path. If either answer is no, the test is a finding. Then ask the two questions a red-green run cannot answer. What can the assertion not see: a matcher scoped to tagged instances reads an untagged one as exempt rather than as a blind spot, an input set built before the producer's own transformation never contains what production emits, and a parity check comparing two copies to each other is blind to what both are missing. What benign change turns it red: an anchor on exact wording, on a positional slice, or on one spelling of a rule usually written another way survives the removal it exists to catch and fails on a rewrite. Anchor on the fact, the identifier, or the enclosing structure.

## Never-shipped migrations

Reject migration scaffolding, version-gated defaults, or old-key carry-forward logic when the underlying preference, schema, or feature first appears in the current unreleased work. Compare with the last published tag. If the key did not ship, use the default path; migration is dead-on-arrival complexity.

## Unknown identifiers

Search every new function, type, variable, asset, command target, and config key that the diff assumes already exists. No result outside the new diff means the dependency is unproven. Dynamic registries require checking their generation or lookup path rather than trusting a name match.

## Dependency changes

Verify lockfile consistency in the project's declared environment. Check the full manifest, overrides, resolver configuration, and dependency diff, then run the project's frozen/locked verification. Regenerate in an isolated checkout only to investigate a concrete discrepancy, using the complete proposed inputs and declared toolchain. Equal bytes do not prove provenance; differences require explanation, not an assumption of hand-editing. Confirm that the resolved dependency graph implements the requested change.

Automated security PRs get two extra checks their scanner does not do. First, whole-file reserialization: bots that rewrite a manifest can silently escape non-ASCII (emoji, CJK) or reorder keys, so diff the manifest against base in full rather than only the version line. Second, reachability: confirm the package is actually built into the shipped artifact (`cargo tree -i <pkg> --target all`, or the equivalent import/feature check) before repeating the advisory's severity, since an inert lockfile entry is not a live vulnerability in this project.

When a version pin exists, find out why before moving anything near it: `git log -S'<pinned-name>' -- <manifest>` usually names the bug it was added for. A bump that satisfies the advisory but leaves a companion pin at its old version can be worse than not bumping at all.
