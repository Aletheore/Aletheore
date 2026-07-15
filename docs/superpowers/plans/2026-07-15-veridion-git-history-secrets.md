# Veridion Git-History Secret Scanning Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Detect secrets that were committed and later removed by walking full git history,
reusing the existing tree-scan's pattern list, redaction, and placeholder heuristic as-is.

**Architecture:** One new function, `find_secrets_in_history`, added directly to
`secrets.py` (same module as `find_secrets` — keeps secrets-domain logic together). It streams
`git log -p` output line-by-line via `subprocess.Popen` (not `subprocess.run(capture_output=True)`,
which would buffer the entire ~54MB of diff output on a repo Procta's size before parsing even
starts) and regex-matches `SECRET_PATTERNS` against added lines only.

**Tech Stack:** Python stdlib only (`subprocess`). No new dependencies, no network calls
anywhere in this plan — entirely local git operations.

## Global Constraints

- Reuse `secrets.py`'s existing `SECRET_PATTERNS`, `_redact`, `_is_likely_placeholder` by
  direct reference — do not reimplement or duplicate any of this logic.
- Stream `git log -p` output — never buffer the full output before parsing (`subprocess.Popen`
  with line iteration over `process.stdout`, not `capture_output=True`).
- No deduplication logic — diff semantics (a line only appears as `+` when genuinely added)
  already keep repeated reporting sparse.
- No merge-commit diff scanning — rely on `git log -p`'s own default behavior (no `-m` flag),
  don't override it.
- `scan_git_history` defaults to `True` at every layer (function parameter, CLI flag), matching
  the measured justification in the design spec (under 5 seconds on Procta's real
  1,703-commit history) — this is `--no-scan-git-history` (opt-out), not opt-in.

---

## Task 1: `find_secrets_in_history`

**Files:**
- Modify: `prototype/veridion/secrets.py`
- Test: `prototype/tests/test_secrets_history.py`

**Interfaces:**
- Consumes: `SECRET_PATTERNS`, `_redact`, `_is_likely_placeholder` (already in `secrets.py`) by
  direct reference — no new imports needed for these.
- Produces: `find_secrets_in_history(repo_path: Path) -> dict` returning
  `{"history_scanned_commits": int, "history_findings": list[dict]}`, where each finding is
  `{"commit": str, "commit_date": str, "path": str, "pattern": str, "match_preview": str,
  "likely_placeholder": bool}`. Task 2 calls this function by this exact name and return shape.

- [ ] **Step 1: Write the failing tests**

Create `prototype/tests/test_secrets_history.py`:
```python
import os
import subprocess
from pathlib import Path

from veridion.secrets import find_secrets_in_history


def run(repo: Path, *args: str):
    subprocess.run(["git", *args], cwd=repo, check=True, capture_output=True)


def commit(repo: Path, message: str, date: str):
    env = os.environ.copy()
    env["GIT_AUTHOR_DATE"] = date
    env["GIT_COMMITTER_DATE"] = date
    subprocess.run(
        ["git", "commit", "-m", message], cwd=repo, check=True, capture_output=True, env=env
    )


def head_hash(repo: Path) -> str:
    result = subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=repo, check=True, capture_output=True, text=True
    )
    return result.stdout.strip()


def init_repo(tmp_path: Path) -> Path:
    repo = tmp_path / "repo"
    repo.mkdir()
    run(repo, "init", "-b", "main")
    run(repo, "config", "user.email", "a@example.com")
    run(repo, "config", "user.name", "Alice")
    return repo


def test_find_secrets_in_history_finds_a_secret_added_then_removed(tmp_path):
    repo = init_repo(tmp_path)

    (repo / "main.py").write_text("x = 1\n")
    run(repo, "add", "main.py")
    commit(repo, "first", "2026-06-01T00:00:00+00:00")

    (repo / "main.py").write_text('x = 1\nAWS_KEY = "AKIAABCDEFGHIJKLMNOP"\n')
    run(repo, "add", "main.py")
    commit(repo, "add key", "2026-06-02T00:00:00+00:00")
    add_key_commit = head_hash(repo)

    (repo / "main.py").write_text("x = 1\n")
    run(repo, "add", "main.py")
    commit(repo, "remove key", "2026-06-03T00:00:00+00:00")

    result = find_secrets_in_history(repo)

    assert len(result["history_findings"]) == 1
    finding = result["history_findings"][0]
    assert finding["commit"] == add_key_commit
    assert finding["path"] == "main.py"
    assert finding["pattern"] == "aws_access_key_id"
    assert "AKIAABCDEFGHIJKLMNOP" not in finding["match_preview"]
    assert finding["match_preview"].startswith("AKIA")
    assert finding["likely_placeholder"] is False
    assert result["history_scanned_commits"] == 3


def test_find_secrets_in_history_does_not_scan_merge_commit_diffs(tmp_path):
    repo = init_repo(tmp_path)

    (repo / "a.txt").write_text("base\n")
    run(repo, "add", "a.txt")
    commit(repo, "base", "2026-06-01T00:00:00+00:00")

    run(repo, "checkout", "-b", "feature")
    (repo / "secret.py").write_text('API_KEY = "AKIAABCDEFGHIJKLMNOP"\n')
    run(repo, "add", "secret.py")
    commit(repo, "add secret on feature branch", "2026-06-02T00:00:00+00:00")

    run(repo, "checkout", "main")
    (repo / "other.py").write_text("y = 2\n")
    run(repo, "add", "other.py")
    commit(repo, "unrelated main work", "2026-06-03T00:00:00+00:00")

    run(repo, "merge", "feature", "-m", "merge feature", "--no-edit")

    result = find_secrets_in_history(repo)

    # exactly one finding - from the original feature-branch commit. The merge
    # commit itself must not produce an additional (duplicate) finding.
    assert len(result["history_findings"]) == 1


def test_find_secrets_in_history_returns_zero_when_no_commits(tmp_path):
    repo = tmp_path / "empty"
    repo.mkdir()
    run(repo, "init", "-b", "main")

    result = find_secrets_in_history(repo)

    assert result == {"history_scanned_commits": 0, "history_findings": []}
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
cd prototype && pytest tests/test_secrets_history.py -v
```
Expected: FAIL (`find_secrets_in_history` doesn't exist yet).

- [ ] **Step 3: Implement `find_secrets_in_history`**

In `prototype/veridion/secrets.py`, add `import subprocess` at the top, and add the function
after `find_secrets`:
```python
def find_secrets_in_history(repo_path: Path) -> dict:
    process = subprocess.Popen(
        ["git", "log", "-p", "--format=COMMIT_START\x1f%H\x1f%ad", "--date=iso-strict"],
        cwd=repo_path,
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
        text=True,
        errors="ignore",
    )

    findings: list[dict] = []
    scanned_commits: set[str] = set()
    current_commit: str | None = None
    current_commit_date: str | None = None
    current_file: str | None = None

    assert process.stdout is not None
    for raw_line in process.stdout:
        line = raw_line.rstrip("\n")
        if line.startswith("COMMIT_START\x1f"):
            parts = line.split("\x1f")
            current_commit = parts[1] if len(parts) > 1 else None
            current_commit_date = parts[2] if len(parts) > 2 else None
            if current_commit:
                scanned_commits.add(current_commit)
            continue
        if line.startswith("+++ b/"):
            current_file = line[len("+++ b/"):]
            continue
        if line.startswith("+++"):
            continue
        if not line.startswith("+"):
            continue

        content = line[1:]
        for pattern_name, pattern, value_group in SECRET_PATTERNS:
            match = pattern.search(content)
            if match:
                findings.append(
                    {
                        "commit": current_commit,
                        "commit_date": current_commit_date,
                        "path": current_file,
                        "pattern": pattern_name,
                        "match_preview": _redact(match.group(value_group)),
                        "likely_placeholder": _is_likely_placeholder(current_file or ""),
                    }
                )

    process.stdout.close()
    process.wait()

    if process.returncode != 0:
        return {"history_scanned_commits": 0, "history_findings": []}

    return {"history_scanned_commits": len(scanned_commits), "history_findings": findings}
```

Note: `process.returncode != 0` covers both "not a git repository" and "repository has no
commits yet" (`git log` fails with a fatal error in both cases) — same graceful-degradation
shape as `git_intel.analyzer`'s `available: False` handling, just returning zeroed/empty
history fields instead of a top-level `available` flag, since `history_scanned_commits: 0,
history_findings: []` is already an unambiguous "nothing to report" signal on its own.

- [ ] **Step 4: Run tests to verify they pass**

```bash
cd prototype && pytest tests/test_secrets_history.py -v
```
Expected: PASS (3 tests).

- [ ] **Step 5: Commit**

```bash
git add prototype/veridion/secrets.py prototype/tests/test_secrets_history.py
git commit -m "feat: add git-history secret scanning"
```

---

## Task 2: Wire `scan_git_history` into `evidence.py`

**Files:**
- Modify: `prototype/veridion/evidence.py`
- Modify: `prototype/tests/test_evidence.py`

**Interfaces:**
- Consumes: `find_secrets_in_history` (Task 1) by its exact name and return shape.
- Produces: `scan_repository` gains a new parameter `scan_git_history: bool = True` (mirroring
  `check_vulnerabilities`'s existing shape exactly). `evidence["security"]["secrets"]` gains
  `history_scanned_commits` and `history_findings` keys alongside the existing
  `scanned_files`/`findings`.

- [ ] **Step 1: Write the failing test**

Append to `prototype/tests/test_evidence.py`:
```python
def test_scan_repository_includes_history_findings_in_secrets_block(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    subprocess.run(["git", "init", "-b", "main"], cwd=repo, check=True, capture_output=True)
    (repo / "main.py").write_text("x = 1\n")

    with patch("veridion.evidence.check_dependency_vulnerabilities") as mock_check:
        mock_check.return_value = {"checked": True, "reason": None, "findings": []}
        evidence = scan_repository(repo)

    secrets = evidence["security"]["secrets"]
    assert "history_scanned_commits" in secrets
    assert "history_findings" in secrets


def test_scan_repository_skips_history_scan_when_disabled(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "main.py").write_text("x = 1\n")

    with patch("veridion.evidence.check_dependency_vulnerabilities") as mock_check:
        mock_check.return_value = {"checked": True, "reason": None, "findings": []}
        with patch("veridion.evidence.find_secrets_in_history") as mock_history:
            evidence = scan_repository(repo, scan_git_history=False)

    mock_history.assert_not_called()
    secrets = evidence["security"]["secrets"]
    assert secrets["history_scanned_commits"] == 0
    assert secrets["history_findings"] == []
```

Check the top of `prototype/tests/test_evidence.py` for its existing `import subprocess` and
`from unittest.mock import patch` — add `import subprocess` if not already present, reuse
`patch` either way.

- [ ] **Step 2: Run tests to verify they fail**

```bash
cd prototype && pytest tests/test_evidence.py -v
```
Expected: FAIL (`history_scanned_commits`/`history_findings` don't exist in the secrets block
yet; `scan_git_history` parameter doesn't exist).

- [ ] **Step 3: Modify `evidence.py`**

Update the import:
```python
from veridion.secrets import find_secrets, find_secrets_in_history
```

Update `scan_repository`'s signature and body:
```python
def scan_repository(
    repo_path: Path, check_vulnerabilities: bool = True, scan_git_history: bool = True
) -> dict:
    ...
    secrets_data = find_secrets(repo_path)
    if scan_git_history:
        history_data = find_secrets_in_history(repo_path)
    else:
        history_data = {"history_scanned_commits": 0, "history_findings": []}
    secrets_data = {**secrets_data, **history_data}
    ...
```
(Insert the `scan_git_history`/`history_data` block right after the existing
`secrets_data = find_secrets(repo_path)` line, before `clusters, cross_cluster_edges =
build_clusters(...)`. The `"security": {"secrets": secrets_data, ...}` line in the return
dict stays unchanged — `secrets_data` already carries the merged keys.)

- [ ] **Step 4: Run tests to verify they pass**

```bash
cd prototype && pytest -v
```
Expected: PASS — full suite, no regressions in any earlier task.

- [ ] **Step 5: Commit**

```bash
git add prototype/veridion/evidence.py prototype/tests/test_evidence.py
git commit -m "feat: wire git-history secret scanning into scan_repository"
```

---

## Task 3: `--no-scan-git-history` CLI flag

**Files:**
- Modify: `prototype/veridion/cli.py`
- Modify: `prototype/tests/test_cli.py`

**Interfaces:**
- Consumes: `scan_repository`'s new `scan_git_history` parameter (Task 2).
- Produces: `_scan(repo_path: str, check_vulnerabilities: bool, scan_git_history: bool) ->
  tuple[int, dict, Path]` and `_audit(repo_path: str, forced_agent: str | None,
  check_vulnerabilities: bool, scan_git_history: bool) -> int` — both gain a new required
  parameter (no default at the function level, matching how `check_vulnerabilities` already
  has no function-level default, only an argparse-level one). No existing test calls `_scan`
  or `_audit` directly (verified — all existing CLI tests go through `main()` with
  `monkeypatch.setattr(sys, "argv", ...)`), so this is a safe, non-breaking signature change
  as long as every `main()` dispatch call site is updated to match.

- [ ] **Step 1: Write the failing test**

Append to `prototype/tests/test_cli.py`:
```python
def test_main_audit_threads_no_scan_git_history_flag(tmp_path, monkeypatch):
    repo = tmp_path
    (repo / "main.py").write_text("x = 1\n")
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "veridion", "audit", str(repo),
            "--no-check-vulnerabilities", "--no-scan-git-history", "--agent", "nonexistent",
        ],
    )

    main()

    evidence = json.loads((repo / ".veridion" / "evidence.json").read_text())
    assert evidence["security"]["secrets"]["history_scanned_commits"] == 0
    assert evidence["security"]["secrets"]["history_findings"] == []
```

Check the top of `prototype/tests/test_cli.py` for its existing `import json`/`import sys` —
reuse them.

- [ ] **Step 2: Run test to verify it fails**

```bash
cd prototype && pytest tests/test_cli.py -v
```
Expected: FAIL (`--no-scan-git-history` is not a recognized argument yet).

- [ ] **Step 3: Wire the flag into `cli.py`**

Update `_scan` and `_audit`:
```python
def _scan(repo_path: str, check_vulnerabilities: bool, scan_git_history: bool) -> tuple[int, dict, Path]:
    repo = Path(repo_path).resolve()
    print(f"Scanning {repo}...")
    evidence = scan_repository(
        repo, check_vulnerabilities=check_vulnerabilities, scan_git_history=scan_git_history
    )
    evidence_path = write_evidence(evidence, repo)
    print(f"Evidence written to {evidence_path}")
    return 0, evidence, evidence_path


def _audit(
    repo_path: str, forced_agent: str | None, check_vulnerabilities: bool, scan_git_history: bool
) -> int:
    _exit_code, _evidence, evidence_path = _scan(repo_path, check_vulnerabilities, scan_git_history)
    repo = Path(repo_path).resolve()
    ...
```
(The rest of `_audit`'s body — adapter selection, `run_reasoning_phase`, error handling,
`SPONSOR_NOTE` print — is unchanged; only the signature and the `_scan` call at the top
change.)

Add the flag to both subparsers in `main()`:
```python
    audit_parser.add_argument(
        "--no-scan-git-history",
        dest="scan_git_history",
        action="store_false",
        default=True,
        help="skip walking git history for secrets (on by default)",
    )
```
(add this block right after `audit_parser`'s existing `--no-check-vulnerabilities` block), and
the identical block again after `scan_parser`'s existing `--no-check-vulnerabilities` block
(same four lines, `scan_parser.add_argument(...)` instead of `audit_parser.add_argument(...)`).

Update the dispatch block:
```python
    if args.command == "audit":
        return _audit(args.path, args.agent, args.check_vulnerabilities, args.scan_git_history)
    if args.command == "scan":
        exit_code, _evidence, _evidence_path = _scan(
            args.path, args.check_vulnerabilities, args.scan_git_history
        )
        return exit_code
```

- [ ] **Step 4: Run tests to verify they pass**

```bash
cd prototype && pytest -v
```
Expected: PASS — full suite, no regressions in any earlier task.

- [ ] **Step 5: Commit**

```bash
git add prototype/veridion/cli.py prototype/tests/test_cli.py
git commit -m "feat: add --no-scan-git-history CLI flag"
```

---

## Task 4: Live verification (not automated)

No further code changes — confirms real-world performance and correctness against Procta.
No live agent/LLM call anywhere in this task (this is entirely local git operations), so
**no explicit go-ahead gate is needed here** unlike every previous part's Task 4/5 — only the
timing and spot-check steps below need running.

- [ ] **Step 1: Reinstall the prototype**

```bash
cd /Users/arihantkaul/Documents/GitHub/Veridion/prototype
pip install -e ".[dev]"
```

- [ ] **Step 2: Time the real scan against Procta, confirm the under-10-second criterion**

```bash
python3 -c "
import time
from pathlib import Path
from veridion.secrets import find_secrets_in_history

start = time.time()
result = find_secrets_in_history(Path('/Users/arihantkaul/proctored-browser'))
elapsed = time.time() - start
print(f'elapsed: {elapsed:.2f}s')
print(f'history_scanned_commits: {result[\"history_scanned_commits\"]}')
print(f'history_findings count: {len(result[\"history_findings\"])}')
"
```
Confirm elapsed time is comfortably under 10 seconds (Success Criterion 1 — the design spec's
one-time measurement was under 5 seconds; this re-confirms it's not a fluke).

- [ ] **Step 3: Spot-check redaction on whatever real findings exist**

If `history_findings` is non-empty, print a few entries and confirm `match_preview` never
contains a full unredacted value (same spot-check discipline used for the tree-scan's
findings earlier this session). If `history_findings` is empty, that's a valid, acceptable
result per the design spec — not a failure to chase.
```bash
python3 -c "
from pathlib import Path
from veridion.secrets import find_secrets_in_history
result = find_secrets_in_history(Path('/Users/arihantkaul/proctored-browser'))
for f in result['history_findings'][:10]:
    print(f['commit'][:8], f['path'], f['pattern'], f['match_preview'], f['likely_placeholder'])
"
```

- [ ] **Step 4: Confirm `--no-scan-git-history` measurably skips the subprocess call**

```bash
cd /Users/arihantkaul/Documents/GitHub/Veridion
time veridion scan /Users/arihantkaul/proctored-browser --no-check-vulnerabilities --no-scan-git-history
```
Compare this timing against a run without the flag (from Step 2's measurement, or a fresh
`time veridion scan ... --no-check-vulnerabilities`) — the flagged-off run should be
meaningfully faster, confirming the subprocess call is genuinely skipped, not just that the
fields report zero while the work still happens.

- [ ] **Step 5: Record the outcome**

If all four success criteria from the design spec pass, this feature is done — report back
with the real timing numbers and whatever `history_findings` turned out to contain. If
anything fails, that's the next debugging task, not a new plan.

---

## Self-Review Notes

**Spec coverage:** the streaming mechanism and merge-commit handling (Task 1, with a real
non-conflicting-merge test rather than an artificial conflict scenario), the evidence schema
merge into the existing `secrets` dict (Task 2), the on-by-default flag with
`--no-scan-git-history` naming (Task 3), and all 4 numbered success criteria (Task 4, mapped
1:1) are each covered by a specific task.

**Placeholder scan:** no TBD/TODO; every code block is complete, runnable code, verified
against the exact function signatures and import patterns already present in `secrets.py`,
`evidence.py`, and `cli.py`.

**Type consistency:** `find_secrets_in_history` (Task 1) → consumed by `evidence.py` (Task 2)
with the exact same `{"history_scanned_commits", "history_findings"}` shape throughout.
`scan_git_history` threads through `scan_repository` → `_scan` → `_audit` → `main()`'s
argparse dispatch with the identical parameter name and boolean semantics at every layer, no
renaming across the chain (unlike `check_vulnerabilities`, this one has no naming-collision
risk to guard against, since `find_secrets_in_history` doesn't share a name with any
parameter).
