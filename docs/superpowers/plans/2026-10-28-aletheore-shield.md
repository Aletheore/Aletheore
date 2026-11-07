# Aletheore Shield Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add `aletheore shield`, a free local command that sets up and runs a guard (diff checks), checkpoints (undoable snapshots) and receipts (what an agent session changed) for AI-written changes.

**Architecture:** A new package `src/aletheore/shield/` holds the capabilities as small modules. Hooks are tiny entry points (`python -m aletheore.shield.hooks <event>`) in the style of `evidence_hook.py`: stdlib-light, time-boxed, always exit 0. The CLI (`shield_cli.py`) is a typer group registered in `cli.py` beside `tokens_app`. Checkpoints are git commits under `refs/aletheore/checkpoints/` built with a temporary index, so the user's index, branches and stash are never touched.

**Tech Stack:** Python 3.11 to 3.14, typer (already used), git plumbing via `aletheore.git_exec.git_executable`, `urllib` for registry lookups, pytest.

**Spec:** `docs/superpowers/specs/2026-10-28-aletheore-shield-design.md` (read it first; the section "Calls made after the builder check" binds this plan).

## Global Constraints

- No model call, no account, no hosted state, no new telemetry. Nothing is gated on `installations.plan`; no upsell text.
- Python 3.11 to 3.14. macOS, Linux and Windows are all supported in v1; `.github/workflows/tests.yml` already runs the full `pytest` on `windows-latest` and `macos-latest`, so every test must pass on all three.
- Hooks always exit 0, print nothing on error, and finish within a 2 s hard stop (aim under 500 ms for agent hooks). Registry timeout is 3 s and registry lookups run only at turn-end and pre-commit, never per edit.
- Guard is non-blocking in v1. Output is at most about 12 lines, silent when everything passed and nothing was skipped, and never says "clean" when a check could not run.
- Checkpoint refs live under `refs/aletheore/checkpoints/`. Skip files over 5 MB, stop at 100 MB total per checkpoint, keep the newest 50 and anything under 7 days. Deny-list (in code): `.env*`, `*.pem`, `*.key`, `*.p12`, `id_rsa*`, `credentials*`.
- `shield off --purge` and pruning delete refs only; never run `git gc` or `git prune`.
- Registry lookups send only newly added package names, are on by default, have an off switch (`ALETHEORE_SHIELD_OFFLINE=1` or `registry_lookups: false` in the shield config), and are listed in `network_plan.py`.
- Setup never changes a file without showing the change list and getting a yes, and `shield off` removes exactly what setup added.
- The token savings meter is out of scope; do not touch it or show it in shield.

## Review Focus

- A repository with no commits yet (no `HEAD`): checkpoint takes a parentless snapshot; guard treats the base as empty.
- Bare repo, detached HEAD, linked worktree (`.git` is a file) and a repo mid-rebase or mid-merge: shield does nothing and the status screen says why.
- Paths with spaces, unicode, newlines and CRLF content, and Windows path separators: every git call uses NUL-separated (`-z`) output.
- Two sessions or rapid edits at once: concurrent checkpoints never collide on a ref name and a corrupt state file never crashes a hook.
- Existing `.claude/settings.local.json` that is invalid JSON or holds other hooks; an existing pre-commit hook; `core.hooksPath` pointing elsewhere (husky): merge or print a snippet, never overwrite.

Each line above has its test in the task that owns the code (named in the task).

---

### Task 1: Spike, hook contract and evidence refresh timing

No product code. Produces a notes file that later tasks read.

**Files:**
- Create: `docs/superpowers/notes/2026-10-28-shield-hook-contract.md`

**Interfaces:**
- Produces: the written answers below, as a short table each task can cite.

- [ ] **Step 1: Capture real Claude Code hook payloads.** In a scratch repo, register a throwaway hook script that appends its stdin JSON to a file, on `PreToolUse` (`Edit|MultiEdit|Write`), `PostToolUse`, `Stop` and `SessionEnd`. Run a short session and record, per event: the field names, whether `session_id` and `cwd` are present, and whether the same `session_id` appears across events.
- [ ] **Step 2: Establish what hook output reaches the agent.** For `PostToolUse` and `Stop`, return JSON with `additionalContext` or a block reason and note which of them the agent actually sees, versus text shown only to the user. Record the exact JSON shape that works.
- [ ] **Step 3: Time evidence refresh.** On this repo and one larger repo, run `aletheore scan` once, edit a file, then time `aletheore.watch.rebuild(repo_path, report)` (the incremental rescan used by the background watcher). Record the seconds, and whether it finishes inside 2 s.
- [ ] **Step 4: Write the notes file with these headings:** Event names and payloads; Session id availability; Agent-visible output shape; Evidence refresh timing; Decisions (which fallbacks from the spec's "Hook triggers" and "Evidence freshness" calls apply).
- [ ] **Step 5: Commit** `docs: record shield hook contract and refresh timing`.

---

### Task 2: Shield foundations (git helper, state, deny-list)

**Files:**
- Create: `src/aletheore/shield/__init__.py` (empty docstring module), `src/aletheore/shield/gitutil.py`, `src/aletheore/shield/state.py`, `src/aletheore/shield/denylist.py`
- Test: `src/tests/test_shield_foundations.py`

**Interfaces:**
- Produces:
  - `gitutil.run_git(repo: Path, *args: str, env: dict[str, str] | None = None, input: bytes | None = None, timeout: float = 10.0) -> subprocess.CompletedProcess[bytes]` (never raises on non-zero exit; uses `git_executable()`; raises nothing on timeout, returns returncode 124 with empty output).
  - `gitutil.repo_state(repo: Path) -> RepoState` where `RepoState` is a dataclass `(ok: bool, reason: str | None, git_dir: Path | None, has_head: bool)`; `ok` is False with a plain-words `reason` for: not a repo, bare repo, detached HEAD, rebase or merge in progress.
  - `state.shield_dir(repo: Path, *, create: bool = False) -> Path` (`<repo>/.aletheore/shield`; never creates `.aletheore` unless `create=True`).
  - `state.ShieldConfig` dataclass `(registry_lookups: bool = True)`; `state.load_config(repo) -> ShieldConfig` (missing or corrupt file gives defaults); `state.save_config(repo, config) -> None`; `state.registry_lookups_enabled(repo) -> bool` (env `ALETHEORE_SHIELD_OFFLINE` set to `1`, `true`, `yes` forces False).
  - `state.session_state_path(repo: Path, session_id: str) -> Path | None` (one JSON file per session; sanitizes the id like `evidence_hook._session_state_path`).
  - `denylist.is_denied_path(rel_path: str) -> bool`.

- [ ] **Step 1: Write failing tests:** `test_repo_state_reports_each_unsupported_case` (bare repo, detached HEAD, rebase-in-progress marker dir, not a repo: `ok is False` and `reason` non-empty; fresh repo with no commits: `ok is True`, `has_head is False`; linked worktree where `.git` is a file: `ok is True`); `test_run_git_handles_unicode_and_spaces_in_paths`; `test_corrupt_config_falls_back_to_defaults`; `test_offline_env_disables_registry_lookups`; `test_is_denied_path` over `.env`, `.env.local`, `a/b/server.pem`, `id_rsa.pub`, `credentials.json`, `src/environment.py` (last is False).
- [ ] **Step 2: Run** `cd src && pytest tests/test_shield_foundations.py -v`. Expected: FAIL (modules missing).
- [ ] **Step 3: Implement** the signatures above. Detect rebase and merge by the presence of `rebase-merge`, `rebase-apply`, `MERGE_HEAD` under the resolved git dir (use `git rev-parse --git-dir`, so worktrees work).
- [ ] **Step 4: Run** the tests. Expected: PASS.
- [ ] **Step 5: Commit** `feat: add shield foundations`.

---

### Task 3: Checkpoint capture

**Files:**
- Create: `src/aletheore/shield/checkpoint.py`
- Test: `src/tests/test_shield_checkpoint.py`

**Interfaces:**
- Consumes: `gitutil.run_git`, `gitutil.repo_state`, `denylist.is_denied_path`.
- Produces:
  - `@dataclass(frozen=True) Checkpoint(ref: str, commit: str, tree: str, created_at: datetime, session: str, files: int, skipped: tuple[str, ...])`.
  - `take_checkpoint(repo: Path, session_id: str, *, force: bool = False, now: datetime | None = None, min_interval_s: float = 5.0) -> Checkpoint | None`. Returns None when the repo state is not ok, when the tree equals the latest checkpoint's tree, or when within `min_interval_s` of the previous one (unless `force`).
  - `list_checkpoints(repo: Path, limit: int = 50) -> list[Checkpoint]` newest first.
  - `prune_checkpoints(repo: Path, *, keep: int = 50, keep_days: int = 7, now: datetime | None = None) -> int` (refs deleted; keeps a ref if it is among the newest `keep` or younger than `keep_days`).
  - `changed_since(repo: Path, checkpoint: Checkpoint) -> list[str]` (repo-relative paths that differ between the checkpoint tree and the working tree now).

- [ ] **Step 1: Write failing tests:**
  - `test_checkpoint_leaves_git_state_untouched`: record `git status --porcelain`, `git stash list`, the index file bytes, `git branch -a` and `git reflog` output before and after `take_checkpoint`; all equal.
  - `test_checkpoint_includes_untracked_and_skips_ignored_denied_and_large`: untracked `new.py` is in the tree; a `.gitignore`d file, `.env.local`, and a 6 MB file are not; the 6 MB and `.env.local` names appear in `skipped`.
  - `test_total_cap_stops_at_100mb` using a patched cap constant `MAX_TOTAL_BYTES`.
  - `test_identical_tree_returns_none` and `test_rate_limit_returns_none_unless_forced`.
  - `test_deleted_tracked_file_is_captured_as_absent`.
  - `test_repo_with_no_commits_gets_parentless_checkpoint`.
  - `test_odd_filenames_round_trip` (space, unicode, a name with a newline where the OS allows it).
  - `test_concurrent_checkpoints_use_unique_refs` (two threads, distinct session ids, both refs exist).
  - `test_prune_keeps_newest_and_recent`.
  - `test_unsupported_state_returns_none` (detached HEAD).
- [ ] **Step 2: Run** `cd src && pytest tests/test_shield_checkpoint.py -v`. Expected: FAIL.
- [ ] **Step 3: Implement.** Approach: list files with `ls-files -co --exclude-standard -z`, drop denied, missing-on-disk and over-cap paths, then `update-index --add -z --stdin` against a temp `GIT_INDEX_FILE` outside the repo, `write-tree`, `commit-tree` (parent = latest checkpoint if any), and `update-ref` to `refs/aletheore/checkpoints/<UTC timestamp>-<8 hex of commit>`. The commit message carries the session id and counts. Constants `MAX_FILE_BYTES = 5 * 1024 * 1024`, `MAX_TOTAL_BYTES = 100 * 1024 * 1024`.
- [ ] **Step 4: Run** the tests. Expected: PASS.
- [ ] **Step 5: Commit** `feat: add shield checkpoint capture`.

---

### Task 4: Checkpoint restore (undo)

**Files:**
- Modify: `src/aletheore/shield/checkpoint.py`
- Test: `src/tests/test_shield_restore.py`

**Interfaces:**
- Consumes: Task 3's `Checkpoint`, `take_checkpoint`, `list_checkpoints`.
- Produces:
  - `@dataclass(frozen=True) RestorePlan(to_write: tuple[str, ...], left_in_place: tuple[str, ...])` and `plan_restore(repo: Path, checkpoint: Checkpoint) -> RestorePlan`. `to_write` are files whose content differs from, or is missing compared with, the checkpoint. `left_in_place` are files that exist now but not in the checkpoint; they are never deleted (plan decision, so undo cannot destroy new work).
  - `restore_checkpoint(repo: Path, checkpoint: Checkpoint, session_id: str) -> RestoreResult` where `RestoreResult(restored: tuple[str, ...], safety: Checkpoint | None)`. It first calls `take_checkpoint(..., force=True)` as the safety checkpoint, then writes each `to_write` file from the checkpoint blob. It never runs reset, checkout of branches, or clean.

- [ ] **Step 1: Write failing tests:** `test_restore_recovers_overwritten_uncommitted_file`; `test_restore_is_itself_undoable` (restore, then restore the safety checkpoint, original bytes back); `test_files_created_after_checkpoint_are_left_in_place`; `test_restore_touches_only_differing_files` (mtime of an identical file unchanged); `test_restore_does_not_move_branch_or_index`; `test_restore_preserves_crlf_bytes`.
- [ ] **Step 2: Run** `cd src && pytest tests/test_shield_restore.py -v`. Expected: FAIL.
- [ ] **Step 3: Implement** `plan_restore` by diffing the checkpoint tree against a fresh temp-index tree of the current state; write files with binary-safe `git cat-file blob` output, creating parent directories.
- [ ] **Step 4: Run** the tests. Expected: PASS.
- [ ] **Step 5: Commit** `feat: add shield checkpoint restore`.

---

### Task 5: New-dependency detection

**Files:**
- Create: `src/aletheore/shield/deps.py`, `src/aletheore/data/import_to_dist.json`
- Test: `src/tests/test_shield_deps.py`

**Interfaces:**
- Consumes: `gitutil.run_git`, `aletheore.vulnerabilities.collect_pins(repo_path) -> (pins, manifests_by_pin, info)` where a pin is `(ecosystem, name, version)`, `aletheore.manifests.is_project_manifest`.
- Produces:
  - `@dataclass(frozen=True) DepRef(ecosystem: str, name: str, source: str, where: str)` with `source` in `"manifest"` or `"import"`.
  - `@dataclass(frozen=True) NotChecked(name: str, reason: str)`.
  - `added_dependencies(repo: Path, base: str = "HEAD") -> tuple[list[DepRef], list[NotChecked]]`.

- [ ] **Step 1: Write failing tests:**
  - `test_new_manifest_entry_is_reported`: add `fastjsonx==1.0` to `requirements.txt` over a committed base; one `DepRef("PyPI", "fastjsonx", "manifest", "requirements.txt")`. Same for a `package.json` dependency (npm).
  - `test_dependency_already_in_base_is_not_new`.
  - `test_workspace_and_local_packages_are_skipped`: an npm workspace member name and a Poetry or uv path dependency produce no `DepRef`.
  - `test_import_only_addition_in_mapped_name_resolves`: `import yaml` resolves to `pyyaml`; `from PIL import Image` to `pillow`.
  - `test_unmapped_import_is_reported_as_import_source_when_not_stdlib_or_local`; `test_stdlib_and_relative_and_repo_local_imports_are_ignored` (`os`, `from . import x`, a top-level package that exists in the repo).
  - `test_unsupported_ecosystem_yields_not_checked` (a `go.mod` addition: `NotChecked` with reason naming the ecosystem).
  - `test_private_index_config_yields_not_checked` (a `pip.conf` or `.npmrc` registry line present).
  - `test_repo_without_head_treats_base_as_empty`.
- [ ] **Step 2: Run** `cd src && pytest tests/test_shield_deps.py -v`. Expected: FAIL.
- [ ] **Step 3: Implement.** Base pins: materialize the base commit's manifest files (`ls-tree -r -z`, `show`) into a temp directory and call `collect_pins` on it, then subtract from the working-tree pins by `(ecosystem, name)`. Imports: parse added lines of changed `.py` (via `ast` on the full file) and `.js/.jsx/.ts/.tsx` (bare specifiers from `import ... from` and `require`) files; keep only specifiers not already imported by the base version of that file. Workspace detection reads npm/pnpm workspaces, Poetry and uv path dependencies; anything uncertain is treated as local. `import_to_dist.json` maps import names to PyPI distribution names (identity entries included for popular packages). Only PyPI and npm are checked; other manifest ecosystems return `NotChecked`.
- [ ] **Step 4: Run** the tests. Expected: PASS.
- [ ] **Step 5: Commit** `feat: detect newly added dependencies for shield`.

---

### Task 6: Registry lookup, near-miss, network disclosure

**Files:**
- Create: `src/aletheore/shield/registry.py`, `src/aletheore/shield/nearmiss.py`, `src/aletheore/data/popular_packages.json`
- Modify: `src/aletheore/network_plan.py`
- Test: `src/tests/test_shield_registry.py`

**Interfaces:**
- Produces:
  - `@dataclass(frozen=True) RegistryInfo(exists: bool | None, first_published: datetime | None, reason: str | None)` (`exists` is None when it could not be checked; `reason` says why).
  - `registry.lookup_package(ecosystem: str, name: str, *, timeout: float = 3.0, cache_path: Path | None = None, opener: Callable | None = None, now: datetime | None = None) -> RegistryInfo`. `ecosystem` is `"PyPI"` or `"npm"`. Cache TTL 24 h. `opener` replaces `urllib.request.urlopen` in tests.
  - `nearmiss.near_miss(ecosystem: str, name: str) -> str | None` returns the popular package within edit distance 1 (names of length 5 or more only) when `name` is not itself popular.
  - `network_plan.shield_network_plan(*, registry_lookups: bool) -> list[dict]` with the same dict shape as `scan_network_plan` (`service`, `enabled`, `sends`, `disable`).

- [ ] **Step 1: Write failing tests:** `test_pypi_404_means_not_exists` and `test_pypi_200_returns_first_published` (earliest release upload time); `test_npm_404_and_200`; `test_timeout_and_network_error_mean_not_checked` (`exists is None`, reason mentions offline or timeout, never False); `test_rate_limited_429_means_not_checked`; `test_cache_hit_makes_no_request`; `test_near_miss_flags_reqeusts_but_not_requests`; `test_popular_list_loads_offline` (`requests` in PyPI list, `react` in npm list); `test_shield_network_plan_lists_only_package_names_and_the_off_switch`.
- [ ] **Step 2: Run** `cd src && pytest tests/test_shield_registry.py -v`. Expected: FAIL.
- [ ] **Step 3: Implement.** Endpoints: `https://pypi.org/pypi/<name>/json` and `https://registry.npmjs.org/<name>`. Send only the name, with a fixed `User-Agent` of `aletheore-shield`. `popular_packages.json` is a committed snapshot (the top few hundred names per ecosystem) with a `source` and `generated` field; note in the file how it was generated.
- [ ] **Step 4: Run** the tests. Expected: PASS.
- [ ] **Step 5: Commit** `feat: add shield registry lookup and network disclosure`.

---

### Task 7: Evidence-based guard checks

**Files:**
- Create: `src/aletheore/shield/guard_evidence.py`
- Modify: `src/aletheore/tests_for.py` (extract a structured helper), `src/aletheore/cli.py` or a new `src/aletheore/evidence_diff.py` (extract the dict-level comparison behind `aletheore diff`'s `--fail-on-new-*` gates)
- Test: `src/tests/test_shield_guard_evidence.py`

**Interfaces:**
- Consumes: `dependents.dependents_of(evidence, target) -> (list[str], set[str])`, `signature_diff.find_changed_signatures(old_evidence, new_evidence) -> list[dict]`, `evidence.load_evidence(repo)`, `watch.rebuild(repo_path, report)`.
- Produces:
  - `tests_for.candidate_tests(evidence: dict, target: str) -> tuple[list[str], list[str]]` returning `(high_confidence, lower_confidence)` and used by `tests_for_report` unchanged in behaviour.
  - `evidence_diff.new_risks(old: dict, new: dict) -> dict[str, list[dict]]` with keys `secrets`, `vulnerabilities`, `layer_violations`, `cycles`.
  - `@dataclass(frozen=True) Finding(check: str, severity: str, message: str, evidence: str)` (defined here, imported by Task 8) with `check` in `"dependencies"`, `"affected"`, `"signatures"`, `"risks"` and `severity` in `"warn"`, `"info"`.
  - `guard_evidence.refresh_evidence(repo: Path, budget_s: float = 2.0) -> bool` (runs `watch.rebuild` in a worker thread; True if it finished inside the budget).
  - `guard_evidence.evidence_checks(repo: Path, changed: list[str], base_evidence: dict | None, budget_s: float) -> tuple[list[Finding], list[str]]` returning findings and `not_checked` reasons.

- [ ] **Step 1: Write failing tests:** `test_candidate_tests_matches_report_output` (existing `tests_for` tests still pass unchanged); `test_affected_lists_dependents_and_a_test_command` (changed `app/core.py` yields dependents, candidate tests, and a `pytest <paths>` line when the repo has `pytest` config, otherwise a file list); `test_signature_change_uses_file_level_wording` (message says "imports the module", never "calls"); `test_new_cycle_and_new_secret_are_reported`; `test_stale_evidence_is_labelled_and_skips_dependent_checks` (evidence older than a changed file gives a `not_checked` reason and no false "no dependents"); `test_missing_base_evidence_skips_signature_check_with_reason`; `test_refresh_over_budget_returns_false_and_keeps_old_evidence`.
- [ ] **Step 2: Run** `cd src && pytest tests/test_shield_guard_evidence.py tests/test_tests_for.py -v`. Expected: new tests FAIL, existing tests PASS.
- [ ] **Step 3: Implement.** Extract, do not rewrite: move the high/low-confidence split out of `tests_for_report` into `candidate_tests`; move the evidence comparison out of the `diff` command's helper into `evidence_diff.new_risks` (the CLI keeps calling it, behaviour unchanged). Where Task 1's notes say refresh is too slow, `refresh_evidence` is still implemented but guard calls it only at turn-end and pre-commit.
- [ ] **Step 4: Run** the tests plus `pytest tests/test_cli.py -q`. Expected: PASS.
- [ ] **Step 5: Commit** `feat: add evidence-based shield guard checks`.

---

### Task 8: Guard assembly and rendering

**Files:**
- Create: `src/aletheore/shield/guard.py`
- Test: `src/tests/test_shield_guard.py`

**Interfaces:**
- Consumes: Tasks 5, 6, 7 (`added_dependencies`, `lookup_package`, `near_miss`, `Finding`, `evidence_checks`), `state.registry_lookups_enabled`.
- Produces:
  - `@dataclass(frozen=True) GuardReport(findings: tuple[Finding, ...], not_checked: tuple[str, ...])`.
  - `guard.run_guard(repo: Path, *, base: str = "HEAD", lookups: bool | None = None, budget_s: float = 2.0, base_evidence: dict | None = None, opener: Callable | None = None) -> GuardReport` (`lookups=None` reads the config and env).
  - `guard.render_report(report: GuardReport, *, max_lines: int = 12) -> str` (empty string when there are no findings and nothing was skipped).

- [ ] **Step 1: Write failing tests:**
  - `test_fixture_diff_yields_one_missing_one_near_miss_and_no_workspace_finding` (the spec's done-criterion: a nonexistent PyPI package, `reqeusts`, and a workspace package in one diff; exactly one "does not exist", one "near-miss", nothing for the workspace package).
  - `test_import_only_missing_name_is_worded_softly` (message says import names can differ from package names).
  - `test_offline_run_reports_not_checked_never_pass` and `test_private_index_reports_not_checked`.
  - `test_recent_first_publish_is_flagged_under_30_days`.
  - `test_render_is_silent_when_clean`; `test_render_caps_at_max_lines_with_a_more_line`; `test_render_mentions_skipped_checks_in_one_line`.
  - `test_run_guard_with_no_commits_uses_empty_base`.
- [ ] **Step 2: Run** `cd src && pytest tests/test_shield_guard.py -v`. Expected: FAIL.
- [ ] **Step 3: Implement** `run_guard`: collect changed files, run Task 5, look up each new manifest dependency (and import-only name) through Task 6 only when lookups are enabled, then Task 7. A manifest dependency with `exists is False` is a `warn` "does not exist on <registry>"; `near_miss` is a `warn`; first published under 30 days is an `info`. Wording states facts with counts and names, no advice, matching `evidence_hook.py`.
- [ ] **Step 4: Run** the tests. Expected: PASS.
- [ ] **Step 5: Commit** `feat: assemble shield guard`.

---

### Task 9: Receipt

**Files:**
- Create: `src/aletheore/shield/receipt.py`
- Test: `src/tests/test_shield_receipt.py`

**Interfaces:**
- Consumes: `checkpoint.list_checkpoints`, `checkpoint.changed_since`, `guard.run_guard`, `guard.render_report`.
- Produces:
  - `@dataclass(frozen=True) Receipt(session: str, created_at: datetime, files: tuple[str, ...], report: GuardReport, first_checkpoint: str | None)`.
  - `receipt.build_receipt(repo: Path, session_id: str, *, now: datetime | None = None) -> Receipt | None` (None when the session has no checkpoint, so nothing is written for an idle session).
  - `receipt.render_markdown(receipt: Receipt) -> str` (files touched, signature changes, dependencies added with verdicts, candidate tests, the checkpoint ref to restore).
  - `receipt.write_receipt(repo: Path, receipt: Receipt) -> Path` into `.aletheore/shield/receipts/<UTC timestamp>-<session>.md`; prunes by the checkpoint retention rule.
  - `receipt.latest_receipt(repo: Path) -> Path | None`.

- [ ] **Step 1: Write failing tests:** `test_receipt_lists_files_changed_since_first_session_checkpoint`; `test_receipt_names_the_restore_checkpoint`; `test_idle_session_writes_nothing`; `test_markdown_is_deterministic_for_fixed_inputs` (snapshot string); `test_latest_receipt_returns_newest`; `test_retention_removes_old_receipts`.
- [ ] **Step 2: Run** `cd src && pytest tests/test_shield_receipt.py -v`. Expected: FAIL.
- [ ] **Step 3: Implement** to the signatures above. If Task 1 found no usable session id or end event, define the session as "since the previous receipt" in `build_receipt` and note it in the Markdown header.
- [ ] **Step 4: Run** the tests. Expected: PASS.
- [ ] **Step 5: Commit** `feat: add shield receipts`.

---

### Task 10: Hook entry points

**Files:**
- Create: `src/aletheore/shield/hooks.py`
- Test: `src/tests/test_shield_hooks.py`

**Interfaces:**
- Consumes: Tasks 3, 8, 9; Task 1's notes for the agent-visible output shape.
- Produces:
  - `hooks.main(argv: list[str] | None = None, stdin=None, stdout=None) -> int` with subcommands `pre-edit`, `turn-end`, `session-end`, `pre-commit`. Always returns 0.
  - `hooks.agent_output(event: str, text: str) -> dict | None` building the JSON Task 1 recorded (or None for events whose output cannot reach the agent).
  - Module runs with `python -m aletheore.shield.hooks <event>`.

- [ ] **Step 1: Write failing tests:** `test_every_subcommand_exits_zero_on_garbage_stdin` (empty, invalid JSON, a JSON list); `test_hook_internal_error_prints_nothing_and_exits_zero` (monkeypatch `take_checkpoint` to raise); `test_pre_edit_takes_a_checkpoint_and_prints_nothing`; `test_turn_end_prints_guard_text_only_when_there_is_something`; `test_session_end_writes_a_receipt`; `test_pre_commit_prints_to_stderr_and_exits_zero`; `test_hook_stops_at_budget` (a patched slow `run_guard` returns within about 2 s with no output); `test_corrupt_session_state_file_is_ignored`; `test_concurrent_pre_edit_hooks_do_not_collide`.
- [ ] **Step 2: Run** `cd src && pytest tests/test_shield_hooks.py -v`. Expected: FAIL.
- [ ] **Step 3: Implement** in the shape of `evidence_hook.main` (same `BaseException` guard, same `ALETHEORE_HOOK_DEBUG` stderr traceback). Run work on a worker thread joined with the 2 s hard stop. `pre-edit` calls `take_checkpoint` and `prune_checkpoints` occasionally (at most once per hour, tracked in session state); only `turn-end` and `pre-commit` call the registry.
- [ ] **Step 4: Run** the tests. Expected: PASS.
- [ ] **Step 5: Commit** `feat: add shield hook entry points`.

---

### Task 11: Setup and removal

**Files:**
- Create: `src/aletheore/hook_command.py`, `src/aletheore/shield/setup.py`
- Modify: `src/aletheore/cli.py` (`hook_evidence_snippet` calls the shared helper; behaviour unchanged)
- Test: `src/tests/test_shield_setup.py`, existing `src/tests/test_cli.py` snippet tests must still pass

**Interfaces:**
- Produces:
  - `hook_command.build_hook_command(argv: list[str], *, platform: str | None = None) -> str` (the quoting logic now inline in `hook_evidence_snippet`, extracted verbatim).
  - `setup.SetupChange(path: Path, kind: str, description: str)` with `kind` in `"create"`, `"edit"`.
  - `setup.plan_setup(repo: Path) -> list[SetupChange] | SetupBlocked` (pure, writes nothing; `SetupBlocked(reason: str)` for non-git or unsupported repo states).
  - `setup.apply_setup(repo: Path) -> None`.
  - `setup.remove_setup(repo: Path, *, purge: bool = False) -> list[Path]` (returns what it removed; `purge` also deletes checkpoint refs only).
  - `setup.shield_status(repo: Path) -> ShieldStatus` dataclass `(enabled: bool, checkpoints: int, latest_receipt: Path | None, notes: tuple[str, ...])`.

- [ ] **Step 1: Write failing tests:**
  - `test_plan_setup_writes_nothing` (directory snapshot equal before and after).
  - `test_apply_then_remove_restores_original_bytes` for a repo with no prior hooks and for one with existing `.claude/settings.local.json` hooks and an existing sh pre-commit hook: other entries and lines are intact.
  - `test_invalid_json_settings_file_is_not_overwritten` (plan returns an edit that prints a snippet, apply refuses and says so).
  - `test_core_hooks_path_is_respected_and_husky_style_setup_prints_snippet_instead`.
  - `test_git_hook_is_a_marked_block_and_runs_on_commit` (create a commit, assert the hook ran; interpreter path with spaces; Windows-style path converted to forward slashes in the sh script).
  - `test_remove_deletes_the_hook_file_only_if_it_held_just_our_block`.
  - `test_shield_off_without_purge_keeps_checkpoint_refs`; `test_purge_deletes_refs_but_never_runs_gc`.
  - `test_build_hook_command_quotes_spaces_for_posix_and_windows`.
- [ ] **Step 2: Run** `cd src && pytest tests/test_shield_setup.py tests/test_cli.py -q`. Expected: shield tests FAIL, existing PASS.
- [ ] **Step 3: Implement.** Plan decision: Claude Code hooks go in `.claude/settings.local.json` (personal, not committed, because the entries hold an absolute interpreter path). Entries: `PreToolUse` matcher `Edit|MultiEdit|Write` running `pre-edit`; the turn-end and session-end events from Task 1's notes; every command contains `aletheore.shield.hooks`, which is how removal finds them. The git hook is written to `git rev-parse --git-path hooks`/`pre-commit` between `# >>> aletheore shield >>>` and `# <<< aletheore shield <<<` markers, executable.
- [ ] **Step 4: Run** the tests. Expected: PASS.
- [ ] **Step 5: Commit** `feat: add shield setup and removal`.

---

### Task 12: The `aletheore shield` command

**Files:**
- Create: `src/aletheore/shield_cli.py`
- Modify: `src/aletheore/cli.py` (register the group beside `tokens_app`, and add `shield` to the home-menu command list near the `("diff", "compare two evidence snapshots")` tuples)
- Test: `src/tests/test_shield_cli.py`; update any `test_cli*.py` that asserts the exact command list

**Interfaces:**
- Consumes: Task 11's `plan_setup`, `apply_setup`, `remove_setup`, `shield_status`; Task 8's `run_guard`, `render_report`; Task 3/4's checkpoint API; Task 9's `latest_receipt`; `network_plan.shield_network_plan`.
- Produces: typer group `shield_app` with `aletheore shield` (setup or status), `aletheore shield undo`, `aletheore shield off [--purge] [--yes]`, `aletheore shield check [--path] [--no-lookups]`.

- [ ] **Step 1: Write failing tests (typer `CliRunner`):** `test_first_run_lists_changes_and_no_changes_on_no` (answer "no", repo unchanged); `test_first_run_yes_installs_and_runs_first_scan_when_missing`; `test_first_run_states_the_network_disclosure` (output mentions package names and the off switch); `test_second_run_shows_status`; `test_non_git_directory_says_so_and_stops`; `test_undo_lists_checkpoints_confirms_then_restores_and_prints_left_in_place_files`; `test_off_removes_everything_and_names_it`; `test_check_prints_nothing_when_clean_and_exit_zero`; `test_check_no_lookups_reports_not_checked`.
- [ ] **Step 2: Run** `cd src && pytest tests/test_shield_cli.py -v`. Expected: FAIL.
- [ ] **Step 3: Implement** with a typer callback using `invoke_without_command=True`; use the existing `console` and prompt conventions in `cli.py` (an explicit "yes" confirmation like `tokens_cli._confirm`). `undo` is interactive only on a TTY; with `--yes` and a checkpoint ref argument it is scriptable.
- [ ] **Step 4: Run** `cd src && pytest tests/test_shield_cli.py tests/test_cli.py -q`. Expected: PASS.
- [ ] **Step 5: Commit** `feat: add aletheore shield command`.

---

### Task 13: Docs, cross-platform check, full suite

**Files:**
- Modify: `README.md` (add a Shield row to "What you can use" and one short section; no price or tier language), `src/README.md` (command reference and the limits), `CHANGELOG.md`, `cspell.json` (new words)
- Test: none new; this task runs the whole suite

**Interfaces:**
- Consumes: everything above.

- [ ] **Step 1: Write the docs.** Cover: the three steps (`pip install aletheore`, `aletheore shield`, one yes); exactly what setup changes; what is checked and what is not (file-level wording, PyPI and npm only, "not checked" outcomes); that registry lookups send package names and how to turn them off; where checkpoints live and the `push --mirror` note; `shield off`; that it makes no model call.
- [ ] **Step 2: Run the full suite with coverage as CI does:** `cd src && pytest --cov=aletheore --cov-fail-under=85`. Expected: PASS.
- [ ] **Step 3: Run spellcheck and markdown lint the way the repo's workflows do** (`spellcheck.yml`, `markdown.yml`); fix findings.
- [ ] **Step 4: Open the pull request and confirm all three platform jobs pass:** `pytest`, `pytest-windows`, `pytest-macos` in `.github/workflows/tests.yml`. Fix Windows-only failures (path separators, file locks during restore, sh hook quoting) in the owning task's module, with a test.
- [ ] **Step 5: Commit** `docs: document aletheore shield`.
