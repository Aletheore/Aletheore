# Release Surface Review Methods

Loaded on demand from `check` when a diff or release question touches one of these surfaces. The fill-in template shapes live in `project-context.md`; this file owns the review method.

## CLI Command Surface

Check command contract and installed-runtime behavior, not just library tests: help/version, subcommands/flags, exit codes, stdout/stderr, JSON/schema output, TTY/non-interactive paths, env/config precedence, shebang/executable bit, PATH shim, and package-manager install path when applicable.

For mutating CLI commands, also run the Safety Sink Review (see `project-context.md`): dry-run or confirmation path, operation log or rollback story, retry/idempotency, signal/partial-failure handling, and test-mode guards for auth prompts or real system changes. For cleanup, uninstall, prune, reset, or cache-removal commands, add two checks before approval: can a normal user verify each selected item is safe, and is the deleted content locally rebuildable rather than a downloaded dependency or user data? If either answer is no, require narrower matching, explicit user selection, or leave the item visible but non-destructive.

## Packaged Install Surface

Verify the installed runtime contract, not just the source tree:

1. Identify the install path a real user will get: package manager, release archive, marketplace entry, plugin source path, or installer script default ref.
2. Build or regenerate the package exactly as project docs require, then inspect the archive or generated mirror for every new script, reference, template, rule, manifest, and executable bit.
3. Run an isolated install smoke when the surface is installable: fresh temp home/config/cache, add the marketplace or package, install the skill or plugin, list it, and invoke the smallest command or entrypoint that proves scripts and references resolve from the installed path.
4. Filter generated mirrors and archives for cache/noise files such as `__pycache__`, `*.pyc`, `.pytest_cache`, `.ruff_cache`, `.mypy_cache`, `.DS_Store`, local logs, and screenshots unless the project explicitly ships them.
5. If network, auth, or host tooling prevents the install smoke, state the missing layer as a blocker or gap. Do not replace installed-runtime proof with manifest JSON, source tests, or a successful local import.

## Reworked Or Cancelled Release Gate

1. Lock the review base to the last public stable tag or release artifact, then review through current `HEAD`. Do not limit the review to recent commits or the latest local diff.
2. Record the exact base, `HEAD`, dirty state, origin sync, version fields, generated artifacts, release notes, package contents, CI, and remote distribution state. If any state changes mid-review, refresh the range and rerun the fast gates.
3. Review by shipped risk surface: user-reported regressions, crash or hang paths, destructive operations, privilege or permission boundaries, background workers, startup or first-frame work, update feeds, package contents, and public support claims.
4. Output two release decisions, not one: whether the preview or beta can keep taking user testing, and whether stable release prep can start.
5. Every conclusion must name blockers, deferrable maintenance, commands that ran, and runtime or user-smoke coverage. Source tests alone cannot prove a reworked UI/native release ready.
