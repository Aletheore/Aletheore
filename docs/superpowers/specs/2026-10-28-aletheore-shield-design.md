# Aletheore Shield: a local safety net for AI-written changes (free tier)

Status: draft for owner review, 2026-10-28. Not yet approved for an implementation plan.

Labels: **[Owner]** is something the owner said or approved in conversation. **[My call]** is a choice
the author made that the owner has not seen. **[Open]** needs an owner decision (last section).
**[Verified]** was checked in the code on 2026-10-28.

## Intent and why

The free tier has grown (scanner, dashboard, MCP, Hallbook, Action, free AI reviews) and **[Owner]**
wants one more thing that many people would install without thinking, that solves a problem they
still have. The problem chosen: agents write code faster than people can check it, and three things
go wrong repeatedly:

- the agent imports a package that does not exist, or is a near-miss of a popular one (hallucinated
  or typosquatted dependencies);
- the agent changes a function or module without knowing what depends on it, or which tests matter;
- the agent overwrites uncommitted work, and the person cannot get it back.

Shield is a single local command, `aletheore shield`, that watches for these and says so briefly.
Install stays `pip install aletheore` (or `pipx`); the only extra step is one confirmation.
**[Owner]** confirmed that flow.

Principle **[My call]**, consistent with `2026-10-28-paid-tier-differentiators-design.md`: free stays
local, deterministic and stateless. Shield makes no model call, needs no account, and keeps state
only in the user's own repo. It does not touch the paid features (review rules and memory,
apply-fix, cross-repo blast radius).

## Goals, non-goals, anti-goals

Goals
- One entry point: `aletheore shield`. First run sets up after showing exactly what it will change
  and getting a yes. Later runs show status.
- Three capabilities behind it: **guard** (checks a diff), **checkpoint** (undoable snapshots),
  **receipt** (what an agent session changed).
- Silent when there is nothing worth saying. When it speaks, it fits on one screen.
- Every claim is inspectable: it says what was checked, what was not, and why.

Non-goals
- The savings meter. **[Owner]**: it is a separate part of Aletheore that runs by itself. Shield
  does not show, compute or depend on it.
- Any model call, hosted state or account.
- Blocking an agent or a commit by default (see Guard output policy).
- Running tests, builds or the user's code.
- Cross-repo analysis, auto-fix, review rules (paid, specified elsewhere).
- Supporting every agent in v1. See Setup.

Anti-goals (a failure even if it technically works)
- Editing a user's agent settings or git hooks without showing the change and getting a yes, or
  being unable to remove everything it added.
- Checkpoint touching the user's branches, index, stash, working tree (except an explicit
  `undo`), or reflog-visible history.
- A hook that breaks, slows or stalls the agent's tool call. Hooks always exit 0 and have a time
  budget, as `evidence_hook.py` already does.
- Noisy false positives. A guard that cries "phantom dependency" on private or workspace packages
  trains users to ignore it. Precision beats coverage.
- Saying "clean" when a check could not run (offline, private registry, stale scan).
- Sending anything beyond new public-looking package names to a registry, or sending anything
  without it being disclosed in the existing network plan.

## Constraints from the existing system

- **[Verified]** `aletheore hook evidence` and `hook evidence-snippet` already exist
  (`src/aletheore/cli.py`, `evidence_hook.py`). The snippet prints a Claude Code `settings.json`
  hooks block; it builds the command from `sys.executable` and quotes it for the platform because
  pipx paths can contain spaces. Shield's setup should reuse this approach, not reinvent it.
- **[Verified]** `evidence_hook.py` documents the hook rules to follow: fast, per-session state,
  stdlib only, always exit 0, silent when nothing to say, and it flags a scan older than the file.
- **[Verified]** `signature_diff.py` detects exported function signature changes between two
  evidence snapshots and lists dependents that import the changed module but were not touched. It is
  **file/import level, not a call graph**; its own docstring says so. User-facing wording must say
  "imports the module", never "calls the function".
- **[Verified]** `tests_for.py` finds candidate tests from import edges and naming; it does not run
  tests or establish coverage. Wording must say "candidate".
- **[Verified]** `aletheore diff` compares two evidence snapshots. Guard needs the same comparison
  between a base (HEAD or merge-base) and the working tree.
- **[Verified]** `network_plan.py` lists every network path of a scan for disclosure (OSV, package
  registries, Semgrep, and so on). Any new registry lookup must be added there.
- **[Verified]** `vulnerabilities.py` and `manifests.py` already parse dependency pins and discover
  manifests for many ecosystems. The new-dependency detection should build on them.
- **[Verified]** No phantom-dependency or typosquat logic exists today.
- Scan freshness: evidence is a snapshot. Guard runs on a working tree that has just changed, so it
  needs fresh evidence for the changed files. How fast that can be done is **unknown**; the builder
  must measure it (see Left to the builder).

## The entry point and setup

`aletheore shield`
- **Not set up in this repo:** show what it found (git repo, scan present or not, which agents it
  can configure), list every file it will create or edit, and ask for a yes. On yes: run the first
  scan if there is none, write the hook entries, write the git pre-commit hook, create the shield
  state directory. On no: change nothing.
- **Set up:** show status: what is on, the latest receipt, the checkpoint count.
- `aletheore shield undo`: list checkpoints and restore one (see Checkpoint).
- `aletheore shield off`: remove exactly what setup added, and say what it removed. Checkpoints
  are kept unless `--purge` is passed.
- `aletheore shield check`: run guard on the current diff on demand, the same code the hooks call.

Decisions
- **Agents in v1 [My call]:** Claude Code (hooks format is already implemented for the evidence
  hook) plus a plain git pre-commit hook, which covers every other agent and humans. Cursor and
  others are a follow-up once the builder verifies their hook contracts; do not guess them.
- **Existing hooks [My call]:** if the user already has hooks or a pre-commit file, merge rather
  than overwrite, and show the diff in the confirmation. If it cannot merge safely, print the
  snippet and ask the user to add it.
- **Scope of changes [My call]:** per repo, never global user settings, unless the user chooses
  otherwise during setup.
- Requires a git repo. In a non-git directory, say so and stop.

## Capability 1: Guard

Runs (a) when the agent finishes a turn and (b) at git pre-commit, on the diff between the base
(HEAD) and the working tree. Also on demand via `shield check`.

### Checks

1. **New or suspicious dependencies.** For each package newly added in the diff, either as a
   manifest entry or as an import that is not stdlib, not a local module, not a workspace package
   and not already in the base lockfile:
   - does it exist on the public registry;
   - is its name within a small edit distance of a very popular package;
   - was it first published very recently (threshold **[My call]**: under 30 days, starting guess).
   First ecosystems **[My call]**: PyPI and npm, where agent-hallucinated names are most reported.
   Others report "not checked".
2. **Who is affected.** Dependents (direct and transitive) of the changed files, and the candidate
   tests among them, rendered as a command for the repo's test runner when it can be inferred
   (for example `pytest <paths>`), otherwise as a file list.
3. **Changes that outrun their dependents.** Signature changes from `signature_diff` with the
   dependents that import the module but were not touched. File-level wording.
4. **New risks in the diff.** New secrets, new known vulnerabilities on added dependencies, new
   import cycles, new layer-convention violations. Reuse the existing checks and their base/head
   comparison.

### Precision rules for check 1 (the likeliest source of harm)

- Skip, and say "not checked: <reason>", for: packages resolved in the repo or workspace; ecosystems
  other than the first two; a repo configured with a private index (`pip.conf`, `.npmrc`
  registry, and similar). A name from a private index must never be reported as "does not exist".
- Only newly added names are sent to the registry, never the whole dependency list, never source,
  never repo names. Add this to `network_plan.py`. Provide a flag and an env var to turn it off, and
  cache results.
- Registry unreachable or rate-limited: report "not checked (offline)", never a pass.
- The popular-names list for near-miss detection is bundled and versioned; its source is left to
  the builder, with the requirement that it works offline.

### Output policy [My call]

- One screen, at most about a dozen lines, grouped by check, with the evidence for each line.
  Silent when everything passes and nothing was skipped. If something was skipped, one line says so.
- **Non-blocking in v1.** The agent hook adds the text to the agent's context so it can correct
  itself, and shows it to the user. The pre-commit hook prints and exits 0. A `--strict` option
  that fails the commit on selected checks is a follow-up, not v1.
- Wording states facts with counts and names, not advice, matching `evidence_hook.py`.

## Capability 2: Checkpoint

### What the user sees

Before an agent edits files, shield quietly saves a snapshot of the working tree. If an agent
overwrites uncommitted work, `aletheore shield undo` shows recent checkpoints (time, files changed
since the previous one) and restores a chosen one.

### Design [My call, builder refines]

- **Storage:** commits under a private ref namespace, `refs/aletheore/checkpoints/...`, built with
  a temporary index file and plumbing commands, so the user's real index, branches, stash and
  working tree are never touched when a checkpoint is taken. Includes tracked and untracked files;
  excludes anything git ignores.
- **Trigger:** before the agent's file-editing tool calls, but skipped if the working tree is
  identical to the latest checkpoint (cheap tree-hash comparison) and rate-limited so a burst of
  edits makes one checkpoint.
- **Limits:** skip files over a size cap and stop at a total cap per checkpoint, saying so in
  `shield` status. Retention starting guess: keep the newest 50 and anything under 7 days, prune the
  rest. Values are starting guesses.
- **Restore:** `undo` first takes a checkpoint of the current state (so undo is undoable), shows
  which files it will change, asks for a yes, then restores only files that differ. Never runs
  `git reset --hard` or similar on the user's branch.
- **Not a git repo, bare repo, or a repo mid-rebase/merge:** do nothing and say so in status.
- **Risk to document:** objects live in `.git`. A `git push --mirror` would push every ref,
  including `refs/aletheore/*`, and those objects can include untracked files that are not ignored.
  Status and setup should state where checkpoints live; the builder should check whether a
  documented configuration can keep the namespace out of mirror pushes.

This is the riskiest part (it touches git state). Design and test it first.

## Capability 3: Receipt

When an agent session ends, shield writes a short deterministic summary to
`.aletheore/shield/receipts/` and shows the latest in `aletheore shield`:

- files touched (diff from the session's first checkpoint to now);
- signature changes and the dependents that import the module but were not touched;
- dependencies added and the guard verdict for each;
- candidate tests to run;
- the checkpoint to restore to undo the session.

No model call. Written as plain Markdown so it can be pasted into a PR description. Retention
follows the checkpoint rule.

## Free-tier boundary

All of shield is free and local. Nothing in it is gated on `installations.plan`. It must not be a
teaser for paid features; no upsell text.

## Success measures [My call]

There is no baseline in the repo. Local-only counts (guard findings shown, checkpoints created,
`undo` uses) can live in the user's repo for the status screen. Whether to add any anonymous
product telemetry is an owner decision (see Open); without it, measure adoption from PyPI downloads
and GitHub issues only.

## Done looks like

- On a fixture repo, `aletheore shield` shows a change list, makes no change on "no", and on "yes"
  installs hooks that `shield off` removes completely.
- An agent-style diff adding a nonexistent PyPI package, a near-miss of a popular package, and a
  workspace package yields exactly one "does not exist", one "near-miss", and no finding for the
  workspace package. Offline and private-index runs report "not checked", never a pass.
- Guard on a diff that changes a signature lists untouched dependents with file-level wording and
  lists candidate tests.
- A checkpoint is taken without changing `git status`, the index, `git stash list` or any branch.
  `undo` restores an overwritten uncommitted file and is itself undoable.
- A hook run that errors, times out or has no evidence prints nothing and exits 0.
- The setup, hooks, checkpoint and `undo` pass on macOS, Linux and a Windows CI runner.

## Calls made after the builder check

A cold reader asked these. None need the owner. All are **[My call]** unless marked verified.

- **Hook triggers and session identity.** **[Verified]** the existing evidence hook already receives
  a session id in its payload and keys per-session state on it (`_session_state_path`,
  `evidence_for_payload` in `evidence_hook.py`) for PreToolUse (`Edit|MultiEdit|Write`) and
  PostToolUse. Not yet verified: that the turn-end and session-end events carry the same id and
  whether turn-end output can reach the agent's context. The builder's **first spike** is to confirm
  both. Defined fallbacks, so the design does not depend on the answer: (a) if guard output cannot
  reach the agent, it is shown to the user and printed at pre-commit; (b) if there is no usable
  session id or end event, a receipt covers "since the previous receipt", and checkpoints are grouped
  by idle gap (starting guess: 30 minutes).
- **Evidence freshness.** No incremental-scan work in the core scanner in v1. Guard uses the
  evidence on disk, states when it is older than the changed files (as `evidence_hook.py` already
  does), and runs only what the evidence supports. Check 1 (dependencies) reads the diff and
  manifests directly, so it never depends on evidence freshness. If refreshing evidence turns out to
  be cheap, the builder may add it; it is not required.
- **Sensitive files in checkpoints.** Snapshots include tracked files and untracked, non-ignored
  files, minus a deny-list kept in code (not config): `.env*`, `*.pem`, `*.key`, `*.p12`,
  `id_rsa*`, `credentials*`, and names matching the secret scanner's file patterns. The status screen
  lists skipped files by name. Same pattern as the apply-fix deny-list in the paid-tier spec.
- **Check 1 scope for v1.** Manifest diffs (newly added dependency entries) are fully checked. A new
  import with no manifest change is checked only when the import name maps to a distribution name
  through a bundled map or an exact match; otherwise the outcome is "not checked: cannot map
  <import> to a package". No guessing. Workspace and local packages are identified from the
  manifests `manifests.py` already discovers plus workspace declarations (npm and pnpm
  workspaces, Poetry and uv path dependencies); anything uncertain is treated as local and skipped.
- **Budgets (starting guesses).** Agent hooks: aim under 500 ms, hard stop at 2 s then exit 0 with no
  output. Registry lookups run only in guard at turn-end and pre-commit, never per edit, with a 3 s
  timeout. Checkpoint: skip files over 5 MB, stop at 100 MB total. Python 3.11 to 3.14, as the
  package supports today. **Platforms: macOS, Linux and Windows in v1 [Owner]** (owner asked for
  Windows on 2026-10-28). The existing hook snippet already has Windows quoting; the builder must
  handle Windows paths, line endings, the git hook (a shell script run by Git for Windows' bundled
  shell), file locking during checkpoint restore, and test the hooks on a Windows CI runner.
- **`shield off --purge` and retention.** Both delete the `refs/aletheore/checkpoints/*` refs only.
  Neither runs `git gc` or prunes, because that would also delete the user's own unreachable
  objects. The objects go away at the user's next normal garbage collection; the command says so
  instead of promising immediate deletion.
- **Open decision 1.** Planned with registry lookups on by default and a one-line off switch, per
  the proposal below. It is a config default, so flipping it is cheap.

## Left to the builder

Hook output contract details for Claude Code; whether and how to refresh evidence for changed
files within the hook time budget; popular-names list source; edit-distance rule; test-runner
inference; checkpoint caps and retention values; receipt layout; naming of state files;
test fixtures; the Windows specifics listed under Budgets.

## Open decisions for the owner

1. Registry lookups of newly added package names by default, or opt-in? Proposed: on by default,
   disclosed in the network plan, with a one-line off switch. It is the only new network traffic.
2. Should guard ever block (agent turn or commit)? Proposed: no in v1, `--strict` later.
3. Any product telemetry for shield, or none beyond existing crash reporting? Proposed: none.
