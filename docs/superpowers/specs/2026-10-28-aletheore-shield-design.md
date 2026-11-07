# Aletheore Shield: a local safety net for AI-written changes (free tier)

Status: implemented on branch `feat/shield-guard` (PR #1161). Written 2026-10-28 as a draft; updated
2026-10-30 so the text matches what was built. Where the build differs from the first draft, the text
below says what was built, and "As built" near the end lists those differences in one place.

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
in the user's own repository (its working tree and its Git directory). The one exception is a bounded
cache of registry answers outside the repository, at `~/.cache/aletheore/shield-registry-cache.json`,
which survives removal (see "Where state lives"). It does not touch the paid features (review rules and memory,
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
  being unable to remove its hooks and settings. Removal keeps a one-line exclude for `.aletheore/`
  while scan evidence remains; delete that directory and run `shield off` again to remove the line.
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
  hooks block built with `hook_config.python_hook`: the exec form, where `command` is
  `sys.executable` and `args` is `-X utf8 -m <module> ...`, so no shell parses it and paths with spaces
  need no quoting. Shield's setup uses the same helper.
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
- `aletheore shield setup` and `aletheore shield status` are the explicit forms of the two cases
  above (the interactive menu reaches them). `setup` on a repo where Shield is on shows only what
  needs updating (for example hook entries in the old string form), or says there is nothing to
  change.
- `aletheore shield undo`: list checkpoints and restore one (see Checkpoint).
- `aletheore shield off`: remove exactly what setup added, and say what it removed. A config file
  that existed before setup is preserved. Checkpoints and receipts are kept unless `--purge` is passed.
  One exception: while scan evidence remains in `.aletheore/`, `off` keeps a one-line `/.aletheore/`
  block in `.git/info/exclude`, so the evidence does not show up in `git status`; delete `.aletheore/`
  and run `off` again to drop it.
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

Runs (a) after each agent edit (without registry lookups), (b) when the agent finishes a turn and
(c) at git pre-commit, on the diff between the base (HEAD) and the working tree. Also on demand via
`shield check`.

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
- **Non-blocking in v1.** The post-edit hook adds the text to the agent's context so it can correct
  itself; the turn-end hook shows it to the user. The pre-commit hook prints and exits 0. A `--strict` option
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
  files, minus a deny-list kept in code (not config): `.env*`, `*.env`, `*.pem`, `*.key`, `*.p12`,
  `id_rsa*`, `credentials` files and more (the full list is under "As built"). These basename patterns
  are matched case-insensitively. The status screen lists skipped files by name. Same pattern as the
  apply-fix deny-list in the paid-tier spec.
- **Check 1 scope for v1.** Manifest diffs (newly added dependency entries) are fully checked. A new
  import with no manifest change is checked only when the import name maps to a distribution name
  through a bundled map or an exact match; otherwise the outcome is "not checked: cannot map
  <import> to a package". No guessing. Workspace and local packages are identified from the
  manifests `manifests.py` already discovers plus workspace declarations (npm and pnpm
  workspaces, Poetry and uv path dependencies); anything uncertain is treated as local and skipped.
- **Budgets (as built).** Every hook has a hard stop, after which it prints nothing and exits 0, and
  the guard inside it has a smaller time budget it checks before each step:
  - per edit (`pre-edit` checkpoint, `post-edit` guard): hard stop 2 s; the post-edit guard budget is
    1.5 s;
  - turn end, session end and pre-commit: hard stop 8 s, guard budget 6 s.
  A step that would start with no time left is reported as "time budget reached; ... skipped", never as
  a pass. Registry lookups run only at turn end, at pre-commit and in `shield check`, never per edit
  and not at session end, with a 3 s timeout each within what is left of the guard budget, after the
  local checks. `shield check` uses the same soft 6 s guard budget but has no outer hard stop: a local
  evidence operation already running can overrun it. Checkpoint: skip files over
  5 MB, stop at 100 MB total. Python 3.11 to 3.14, as the package supports today. **Platforms: macOS,
  Linux and Windows in v1 [Owner]** (owner asked for Windows on 2026-10-28). Agent hooks use the exec
  form, so no shell quoting is involved on any platform; only the Git pre-commit block (a `sh` script,
  run by Git for Windows' bundled `sh`) quotes the interpreter path, with forward slashes on Windows.
  CI runs the unit tests and a CLI smoke test (setup, the four hook entries spawned without a shell,
  `off`) on macOS, Linux and Windows runners.
- **`shield off --purge` and retention.** Both delete the `refs/aletheore/checkpoints/*` refs only.
  Neither runs `git gc` or prunes, because that would also delete the user's own unreachable
  objects. The objects go away at the user's next normal garbage collection; the command says so
  instead of promising immediate deletion.
- **Open decision 1.** Planned with registry lookups on by default and a one-line off switch, per
  the proposal below. It is a config default, so flipping it is cheap.

## As built (2026-10-30)

Differences from the first draft, and the decisions the builder made, in one place.

- **Hooks.** Four Claude Code events: `pre-edit` (PreToolUse, Edit|MultiEdit|Write: checkpoint),
  `post-edit` (PostToolUse, same tools: guard without registry lookups, findings to the agent through
  `additionalContext`, only when they changed), `turn-end` (Stop: guard with lookups, text to the user
  through `systemMessage`, never `decision: block`) and `session-end` (SessionEnd: receipt, without
  lookups). Plus the Git `pre-commit` block. Budgets are under "Budgets" above.
- **Hook entries.** Exec form: `command` is the interpreter and `args` is
  `["-X", "utf8", "-P", "-m", "aletheore.shield.hooks", <event>]`. `-P` because the hook's current folder is the
  repository and `python -m` would otherwise put it first on sys.path, letting a repository's own `aletheore/` or
  `tree_sitter.py` run inside the hook (the `pre-commit` block and the background refresh use `-P` too; the JS
  parse child runs with `-I` and adds only the folders this process imported aletheore, tree_sitter and
  tree_sitter_typescript from, so no repository folder on sys.path reaches it). A hook counts as
  Shield's only in that exact shape, the same without `-P` (earlier builds; setup migrates it), or the old
  four-word string `<python> -m aletheore.shield.hooks <event>` that earlier builds wrote; a user's hook that runs the module any other way is left alone. `setup` replaces an old
  string entry in place and keeps any other fields the user added to it. There is no separate
  hook-command module any more.
- **Commands.** `aletheore shield` (setup when off, status when on), `shield setup`, `shield status`,
  `shield undo`, `shield off [--purge]`, `shield check [--no-lookups]`. Errors and refusals go to stderr
  as `error: ...`. `aletheore --help` and every other command do not import Shield's modules; each
  shield command imports what it uses when it runs (a test pins this).
- **Removal.** `off` restores a settings file byte for byte when nobody changed it since Shield wrote it
  (from a saved copy). Otherwise it removes only Shield's entries, and deletes a settings file Shield
  created only when nothing else is left in it. The `/.aletheore/` exclude line stays while scan
  evidence remains.
- **Where state lives.**
  - In the working tree, under `.aletheore/shield/` (kept out of `git status` by the exclude block):
    `config.json`, `receipts/`, per-session base evidence copies in `sessions/` and the background
    refresh marker and completion record.
  - In the Git directory: checkpoint refs under `refs/aletheore/checkpoints/<main|wt>/...`, and
    `<git dir>/aletheore-shield/` with per-session hook state (`hook-*.json`, removed after 7 days if a
    session never ended), the dependency pins cache and the setup record plus the saved original
    settings.
  - Outside the repository: `~/.cache/aletheore/shield-registry-cache.json` (under the user profile on
    Windows), registry answers for 24 hours. A file over 1 MiB is ignored on read; a write keeps the new
    answer and the newest others, at most 2,048 entries and 1 MiB, so the cache never grows past what it
    will read. `off --purge` leaves it.
- **Locks.** One mechanism: `shield/locking.py` `file_lock`, a bounded (0.25 s) OS lock (`flock` on
  POSIX, `msvcrt.locking` on Windows) that the OS releases when the holder dies, so there are no stale
  lock files to break. Hook state writes, the hourly prune claim, the dedupe ("said") record, the refresh
  marker claim and the registry cache take it. A lock still busy after its wait skips the write (pre-edit
  then warns that turn-end checks may be skipped); it never writes without the lock. Writes themselves go
  through `fsutil.atomic_write_text` (temporary file plus `os.replace`).
- **Import extraction.** Python: `ast`, with a `tokenize` fallback for a file that does not parse. JS/TS:
  the tree-sitter TSX grammar with string escapes decoded. tree-sitter's error recovery can go quadratic on
  hostile text and a parse cannot be stopped safely once started, so text up to 6 KB is parsed in process
  (worst case known about 0.13 s; 0.23 s at 8 KB) and larger text in a child process killed after 1 s (or what is left of the
  budget); that file's imports are then reported as not checked. Git-ignored registry configs (`.npmrc`, `.yarnrc.yml`,
  `pip.conf`, `pip.ini`) in any folder of the working tree are read, so a private registry is never missed;
  an import two unrelated distributions provide (`jwt`, `bson`, ...) with neither declared is not checked.
- **New import cycles.** A new cycle group is familiar (not reported) when it meets exactly one old group
  and holds all of it or only modules of it; when files of that group were deleted, new files joined and
  every surviving member is still in the loop and each deleted member became one of the new files (a rename
  or move: the same file name, or the deleted file's HEAD text and the new file at least 50% alike); or when
  an old group was moved whole (none of its files left, same file names). Joining two old cycles, a new file
  closing a loop that a surviving member left, or a deleted member plus an unrelated new file closing the
  loop, is a new cycle.
- **Git.** Resolved from absolute PATH entries only (never a relative entry or the current directory,
  which is the repository for a hook). Timeouts and spawn failures use sentinel return codes outside
  every exit code, so a git exiting 124 or 127 is never mistaken for either.
- **Pins cache.** The base side's dependency pins are cached per manifest folder in
  `<git dir>/aletheore-shield/pins-cache.json`, keyed by the folder's file names and Git blob ids and the
  Aletheore version that parsed them, so a cached entry is never stale. It is bounded (4,000 folders,
  16 MB), and a corrupt or oversized file is ignored. Only folders holding a changed manifest are parsed
  on the working side.
- **Deny-list.** Never read, hashed or stored in a checkpoint, matched on the lower-cased base name
  (`\` and `/` both separate folders): `.env*`, `*.env`, `<name>.env.<environment or backup name>`
  (local, production, staging, dev, test, qa, ci, preview, secret(s), backup, bak, old and similar;
  `app.env.example`, `values.env.yaml` and similar templates or configuration are kept, and so are
  `.env.example`, `.env.sample` and `.env.template`), `*.pem`, `*.key`, `*.p12`, `*.p8`, `*.pfx`, `*.ppk`,
  `*.keystore`, `*.jks`, `*.tfvars`, `*.tfvars.json`, `terraform.tfstate*`, `service-account*.json`,
  `id_rsa*`, `id_dsa*`, `id_ecdsa*`, `id_ed25519*`, `credentials*` and `.credentials*` (except source
  code such as `credentials.py`), `.git-credentials`, `secrets.json/.yml/.yaml`, `.npmrc`, `.pypirc`,
  `.netrc`. `undo` lists every file the checkpoint did not capture as "not captured, cannot be
  restored".
- **Receipts.** `build_receipt` returns None only when there is nothing worth a receipt. Any other
  failure gives a receipt with no files whose only "not checked" line is `receipt could not be computed
  (<error type>)`; it is written like any receipt, and the session-end hook also prints that line to
  stderr, keeps stdout empty and exits 0. Retention ages receipts by the write-time wall clock; a receipt
  stamped up to a day ahead counts as real.
- **Debugging.** With `ALETHEORE_HOOK_DEBUG=1`, every failure a hook or helper swallows (a state write,
  a corrupt cache, a prune, a failed setup rollback) is explained on stderr; stdout stays the hook's
  JSON or nothing.
- **Known limits.** `pre-edit` snapshots the whole working tree, so it grows with the repository
  (about 0.6 s warm on a 9,400-file repository). Restored files follow the repository's Git line-ending
  and filter settings, as `git checkout` would, so with `autocrlf` or `eol` attributes a file can come back
  with different line endings than it had. Shield has not been run against a real Claude Code session on
  Windows; Windows coverage is the CI unit tests and CLI smoke test.

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
