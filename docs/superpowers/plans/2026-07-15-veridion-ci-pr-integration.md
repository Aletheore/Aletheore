# Veridion CI/PR Integration Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a `veridion diff` CLI command comparing two explicit evidence.json files, and a composite GitHub Action (`action.yml`) wrapping the two-ref-checkout → scan-both → diff flow, so PR-level regression checking works with no local setup.

**Architecture:** `veridion diff` is a thin CLI wrapper around `compute_diff()` (already shipped in `prototype/veridion/history.py` as of commit `aaf8039` — verified present, not assumed) reading two file paths instead of the local rolling-history mechanism `query changes` uses. `action.yml` at the repository root is a declarative composite action with no Python of its own — it shells out to `pip install` + `veridion scan` (twice) + `veridion diff`.

**Tech Stack:** Python 3.11+ stdlib only (`json`, `pathlib`, `argparse`) for the CLI piece — no new dependency, matching this project's existing no-new-dependency discipline. `actions/checkout@v4` and `actions/setup-python@v5` for the GitHub Actions piece — these are GitHub-provided actions, not Python dependencies.

## Global Constraints

- No new evidence.json fields, no scanner changes — confirmed by re-reading the approved spec.
- `--fail-on-new-secrets` only ever gates on real (non-placeholder) secret findings — never layer violations or vulnerabilities, which stay CI-author judgment calls.
- `action.yml` lives at the Veridion repo root (`/Users/arihantkaul/Documents/GitHub/Veridion/action.yml`), not inside `prototype/`.
- Actual Marketplace publishing is a manual, human-only step (clicking "Publish to Marketplace" during a GitHub release) — this plan stops short of it and hands back to the user at that point.

---

### Task 1: `veridion diff` CLI command

**Files:**
- Modify: `prototype/veridion/cli.py`
- Modify: `prototype/tests/test_cli.py`

**Interfaces:**
- Consumes: `compute_diff(old: dict, new: dict, full: bool = False) -> dict` from
  `veridion.history` (already imported in `cli.py` as of the continuity/history round — verify
  the import line `from veridion.history import compute_diff, list_snapshots, save_snapshot`
  is present before starting).
- Produces: `_diff(old_path: str, new_path: str, full: bool, fail_on_new_secrets: bool) -> int`.

**Design note on `--fail-on-new-secrets` combined with `--full`:** the full-diff shape
(`{"added": [...], "removed": [...], "changed": [...]}`) has no `secrets.new`/
`history_secrets.new` buckets to check. Rather than silently ignoring the flag or erroring,
`_diff` always evaluates the fail condition using a **separately computed curated diff**
(calling `compute_diff` a second time with `full=False` when `--full` was requested), while
still printing whichever shape the user asked for. This keeps both flags independently
meaningful rather than picking an arbitrary precedence.

- [ ] **Step 1: Write the failing tests**

Append to `prototype/tests/test_cli.py`:

```python
def make_evidence_file(path: Path, findings: list[dict] | None = None) -> Path:
    evidence = {
        "repository": {"modules": [], "dependency_graph": {"nodes": [], "edges": []}},
        "git": {"total_commits": 0},
        "security": {
            "secrets": {
                "findings": findings or [],
                "history_scanned_commits": 0,
                "history_findings": [],
            },
            "dependency_vulnerabilities": {"checked": True, "reason": None, "findings": []},
        },
        "architecture": {"layer_violations": {"violations": []}},
    }
    path.write_text(json.dumps(evidence))
    return path


def test_main_diff_shows_curated_diff_between_two_files(tmp_path, monkeypatch, capsys):
    old_path = make_evidence_file(tmp_path / "old.json")
    new_path = make_evidence_file(
        tmp_path / "new.json",
        findings=[
            {
                "path": "a.py",
                "pattern": "aws_access_key_id",
                "match_preview": "AKIA...MNOP",
                "likely_placeholder": False,
            }
        ],
    )

    monkeypatch.setattr(sys, "argv", ["veridion", "diff", str(old_path), str(new_path)])
    exit_code = main()

    assert exit_code == 0
    captured = capsys.readouterr()
    result = json.loads(captured.out)
    assert len(result["secrets"]["new"]) == 1


def test_main_diff_full_flag_returns_raw_diff(tmp_path, monkeypatch, capsys):
    old_path = make_evidence_file(tmp_path / "old.json")
    new_path = make_evidence_file(tmp_path / "new.json")

    monkeypatch.setattr(sys, "argv", ["veridion", "diff", str(old_path), str(new_path), "--full"])
    exit_code = main()

    assert exit_code == 0
    captured = capsys.readouterr()
    result = json.loads(captured.out)
    assert set(result.keys()) == {"added", "removed", "changed"}


def test_main_diff_fail_on_new_secrets_exits_1_for_a_real_secret(tmp_path, monkeypatch, capsys):
    old_path = make_evidence_file(tmp_path / "old.json")
    new_path = make_evidence_file(
        tmp_path / "new.json",
        findings=[
            {
                "path": "a.py",
                "pattern": "aws_access_key_id",
                "match_preview": "AKIA...MNOP",
                "likely_placeholder": False,
            }
        ],
    )

    monkeypatch.setattr(
        sys, "argv", ["veridion", "diff", str(old_path), str(new_path), "--fail-on-new-secrets"]
    )
    exit_code = main()

    assert exit_code == 1


def test_main_diff_fail_on_new_secrets_exits_0_for_a_placeholder_only(tmp_path, monkeypatch, capsys):
    old_path = make_evidence_file(tmp_path / "old.json")
    new_path = make_evidence_file(
        tmp_path / "new.json",
        findings=[
            {
                "path": "tests/fixture.py",
                "pattern": "generic_credential_assignment",
                "match_preview": "test****...cret",
                "likely_placeholder": True,
            }
        ],
    )

    monkeypatch.setattr(
        sys, "argv", ["veridion", "diff", str(old_path), str(new_path), "--fail-on-new-secrets"]
    )
    exit_code = main()

    assert exit_code == 0


def test_main_diff_fail_on_new_secrets_works_even_with_full_flag(tmp_path, monkeypatch, capsys):
    old_path = make_evidence_file(tmp_path / "old.json")
    new_path = make_evidence_file(
        tmp_path / "new.json",
        findings=[
            {
                "path": "a.py",
                "pattern": "aws_access_key_id",
                "match_preview": "AKIA...MNOP",
                "likely_placeholder": False,
            }
        ],
    )

    monkeypatch.setattr(
        sys,
        "argv",
        ["veridion", "diff", str(old_path), str(new_path), "--full", "--fail-on-new-secrets"],
    )
    exit_code = main()

    assert exit_code == 1
    captured = capsys.readouterr()
    result = json.loads(captured.out)
    assert set(result.keys()) == {"added", "removed", "changed"}


def test_main_diff_missing_file_errors_cleanly(tmp_path, monkeypatch, capsys):
    old_path = make_evidence_file(tmp_path / "old.json")
    missing_path = tmp_path / "does_not_exist.json"

    monkeypatch.setattr(sys, "argv", ["veridion", "diff", str(old_path), str(missing_path)])
    exit_code = main()

    assert exit_code == 1
    captured = capsys.readouterr()
    assert "not found" in captured.out
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd prototype && python3 -m pytest tests/test_cli.py -v -k main_diff`
Expected: FAIL — argparse errors with `invalid choice: 'diff'` (the `diff` subcommand doesn't
exist yet).

- [ ] **Step 3: Write the implementation**

In `prototype/veridion/cli.py`, add `_diff` right after `_query`:

```python
def _diff(old_path: str, new_path: str, full: bool, fail_on_new_secrets: bool) -> int:
    old_file = Path(old_path)
    new_file = Path(new_path)

    if not old_file.exists():
        print(f"error: evidence file not found: {old_file}")
        return 1
    if not new_file.exists():
        print(f"error: evidence file not found: {new_file}")
        return 1

    try:
        old = json.loads(old_file.read_text())
    except json.JSONDecodeError:
        print(f"error: {old_file} is not valid JSON")
        return 1
    try:
        new = json.loads(new_file.read_text())
    except json.JSONDecodeError:
        print(f"error: {new_file} is not valid JSON")
        return 1

    diff = compute_diff(old, new, full=full)
    print(json.dumps(diff, indent=2))

    if fail_on_new_secrets:
        curated = diff if not full else compute_diff(old, new, full=False)
        new_real_secrets = [
            f for f in curated["secrets"]["new"] if not f.get("likely_placeholder", False)
        ]
        new_real_history_secrets = [
            f for f in curated["history_secrets"]["new"] if not f.get("likely_placeholder", False)
        ]
        if new_real_secrets or new_real_history_secrets:
            return 1

    return 0
```

In `main()`, add the subparser (after the `query_parser` block, before `args = parser.parse_args()`):

```python
    diff_parser = subparsers.add_parser("diff", help="compare two evidence.json files")
    diff_parser.add_argument("old", help="path to the baseline evidence.json")
    diff_parser.add_argument("new", help="path to the comparison evidence.json")
    diff_parser.add_argument(
        "--full",
        action="store_true",
        default=False,
        help="show the full raw diff instead of the curated summary",
    )
    diff_parser.add_argument(
        "--fail-on-new-secrets",
        dest="fail_on_new_secrets",
        action="store_true",
        default=False,
        help="exit 1 if a new real (non-placeholder) secret finding appears",
    )
```

And in the dispatch section:

```python
    if args.command == "diff":
        return _diff(args.old, args.new, args.full, args.fail_on_new_secrets)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd prototype && python3 -m pytest tests/test_cli.py -v -k main_diff`
Expected: 6 passed

Run: `cd prototype && python3 -m pytest -v`
Expected: all pass, no regressions

- [ ] **Step 5: Commit**

```bash
cd prototype && git add veridion/cli.py tests/test_cli.py
git commit -m "feat: add veridion diff command for comparing two evidence files"
```

---

### Task 2: `action.yml` composite GitHub Action

**Files:**
- Create: `action.yml` (repository root — `/Users/arihantkaul/Documents/GitHub/Veridion/action.yml`, NOT inside `prototype/`)

**Interfaces:**
- Consumes: the `veridion diff`/`veridion scan` CLI commands from Task 1 and the existing
  `_scan` path, installed via `pip install ...#subdirectory=prototype` (the package's
  `[project.scripts] veridion = "veridion.cli:main"` entry point, confirmed present in
  `prototype/pyproject.toml`).
- Produces: a composite action referenceable as `uses: ./` from within this repo (for Task 4's
  same-repo testing) and eventually `uses: ArihantK15/Veridion@v1` once tagged.

**Design note on `set -e` and exit-code propagation:** GitHub Actions composite `run:` steps
with `shell: bash` execute with `bash --noprofile --norc -eo pipefail {0}` — `-e` is on by
default, meaning a non-zero exit from `veridion diff` would abort the step immediately,
skipping the `$GITHUB_OUTPUT` write that needs to happen regardless of the diff's exit code.
The Diff step below explicitly disables `-e` around the `veridion diff` invocation, captures
its exit code, re-enables `-e`, writes the output, then manually re-raises the captured exit
code via `exit $DIFF_EXIT_CODE` at the very end — this is intentional, not accidental
error-suppression.

- [ ] **Step 1: Write `action.yml`**

```yaml
name: "Veridion PR Diff"
description: "Evidence-grounded diff of what changed between two refs - new secrets, layer violations, dependency vulnerabilities, and architecture deltas."
author: "ArihantK15"

branding:
  icon: "shield"
  color: "blue"

inputs:
  base-ref:
    description: "Git ref/sha to treat as the base"
    required: false
    default: ${{ github.event.pull_request.base.sha }}
  head-ref:
    description: "Git ref/sha to treat as the head"
    required: false
    default: ${{ github.event.pull_request.head.sha }}
  fail-on-new-secrets:
    description: "Exit non-zero if a new real (non-placeholder) secret is found"
    required: false
    default: "false"
  full:
    description: "Show the full raw diff instead of the curated summary"
    required: false
    default: "false"

outputs:
  diff-json:
    description: "The JSON diff output"
    value: ${{ steps.diff.outputs.diff-json }}

runs:
  using: "composite"
  steps:
    - name: Checkout base
      uses: actions/checkout@v4
      with:
        ref: ${{ inputs.base-ref }}
        path: veridion-base

    - name: Checkout head
      uses: actions/checkout@v4
      with:
        ref: ${{ inputs.head-ref }}
        path: veridion-head

    - name: Set up Python
      uses: actions/setup-python@v5
      with:
        python-version: "3.12"

    - name: Install Veridion
      shell: bash
      run: pip install "git+https://github.com/ArihantK15/Veridion.git@${{ github.action_ref }}#subdirectory=prototype"

    - name: Scan base
      shell: bash
      run: veridion scan veridion-base --no-check-vulnerabilities

    - name: Scan head
      shell: bash
      run: veridion scan veridion-head --no-check-vulnerabilities

    - name: Diff
      id: diff
      shell: bash
      run: |
        ARGS=""
        if [ "${{ inputs.full }}" = "true" ]; then
          ARGS="$ARGS --full"
        fi
        if [ "${{ inputs.fail-on-new-secrets }}" = "true" ]; then
          ARGS="$ARGS --fail-on-new-secrets"
        fi

        set +e
        veridion diff veridion-base/.veridion/evidence.json veridion-head/.veridion/evidence.json $ARGS > diff-output.json
        DIFF_EXIT_CODE=$?
        set -e

        {
          echo "diff-json<<VERIDION_EOF"
          cat diff-output.json
          echo "VERIDION_EOF"
        } >> "$GITHUB_OUTPUT"

        cat diff-output.json
        exit $DIFF_EXIT_CODE
```

- [ ] **Step 2: Validate YAML syntax**

Run:
```bash
cd "/Users/arihantkaul/Documents/GitHub/Veridion"
python3 -c "import yaml; yaml.safe_load(open('action.yml')); print('valid YAML')"
```
Expected: `valid YAML` (this only checks syntax, not GitHub Actions semantics — real behavior is
verified live in Task 4). Note: this uses whatever PyYAML happens to be available in the local
dev shell for this one-off manual check — it is not added as a project dependency, since
`action.yml` itself is not parsed by any Python code Veridion ships.

- [ ] **Step 3: Manual read-through against the spec**

Re-read `docs/superpowers/specs/2026-07-15-veridion-ci-pr-integration-design.md`'s "action.yml:
Composite GitHub Action" section side-by-side with the file just written. Confirm: all 4 inputs
present with correct defaults, the `diff-json` output wired to `steps.diff.outputs.diff-json`,
all 7 steps present in the documented order, the `github.action_ref` pinning present in the
install step exactly as specified.

- [ ] **Step 4: Commit**

```bash
cd "/Users/arihantkaul/Documents/GitHub/Veridion"
git add action.yml
git commit -m "feat: add composite GitHub Action for PR diffing"
```

---

### Task 3: Live verification

Not automated — no live agent call needed, matching the pattern used for every prior
increment's final task this session.

- [ ] **Step 1: `veridion diff` against two real evidence.json files**

```bash
cd "/Users/arihantkaul/Documents/GitHub/Veridion/prototype"
ls /Users/arihantkaul/proctored-browser/.veridion/history/*.json | tail -2
```

Take the two most recent paths printed above and run:

```bash
python3 -m veridion.cli diff <older-path> <newer-path>
```

Expected: valid JSON matching `compute_diff`'s curated shape, consistent with what
`veridion query changes --path /Users/arihantkaul/proctored-browser` already showed earlier
this session for the same two snapshots (cross-check the two outputs are identical — they
should be, since both call the same underlying function on the same two files).

- [ ] **Step 2: `--fail-on-new-secrets` exit code check against synthetic files**

```bash
SCRATCH=/private/tmp/claude-501/-Users-arihantkaul-Desktop-AI-Face-Detect-Base-Code/1788d1c3-3517-4ca5-a065-d785dce2edbc/scratchpad/diff-check
rm -rf "$SCRATCH" && mkdir -p "$SCRATCH"
python3 -c "
import json
base = {
    'repository': {'modules': [], 'dependency_graph': {'nodes': [], 'edges': []}},
    'git': {'total_commits': 0},
    'security': {
        'secrets': {'findings': [], 'history_scanned_commits': 0, 'history_findings': []},
        'dependency_vulnerabilities': {'checked': True, 'reason': None, 'findings': []},
    },
    'architecture': {'layer_violations': {'violations': []}},
}
json.dump(base, open('$SCRATCH/old.json', 'w'))
with_real = json.loads(json.dumps(base))
with_real['security']['secrets']['findings'] = [
    {'path': 'a.py', 'pattern': 'aws_access_key_id', 'match_preview': 'AKIA...MNOP', 'likely_placeholder': False}
]
json.dump(with_real, open('$SCRATCH/new_real.json', 'w'))
with_placeholder = json.loads(json.dumps(base))
with_placeholder['security']['secrets']['findings'] = [
    {'path': 'tests/fixture.py', 'pattern': 'generic_credential_assignment', 'match_preview': 'test****...cret', 'likely_placeholder': True}
]
json.dump(with_placeholder, open('$SCRATCH/new_placeholder.json', 'w'))
"
python3 -m veridion.cli diff "$SCRATCH/old.json" "$SCRATCH/new_real.json" --fail-on-new-secrets; echo "exit: $?"
python3 -m veridion.cli diff "$SCRATCH/old.json" "$SCRATCH/new_placeholder.json" --fail-on-new-secrets; echo "exit: $?"
```

Expected: first command prints `exit: 1`, second prints `exit: 0`.

- [ ] **Step 3: End-to-end GitHub test via `uses: ./` (same-repo reference)**

This step requires a real GitHub repository with the `action.yml` pushed and a real pull
request — it cannot be simulated locally. Requires `gh` CLI access and a decision from the
user on which repository to test against (this Veridion repo itself, once `action.yml` is
pushed, is the natural choice since it already has real PRs).

1. Push the current branch (containing `action.yml` and the `diff` command) so it's reachable.
2. Add a temporary test workflow (not committed permanently — or committed and removed after
   verification, user's call) at `.github/workflows/veridion-diff-test.yml`:

```yaml
name: Veridion Diff Test
on:
  pull_request:
jobs:
  test:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - uses: ./
        id: veridion
        with:
          fail-on-new-secrets: "false"
      - run: echo "${{ steps.veridion.outputs.diff-json }}"
```

3. Open a real (throwaway, e.g. a one-line README change) pull request against this repo and
   confirm the workflow runs, both checkout steps succeed, and the diff output appears in the
   Actions log and as the `diff-json` output.

**This step needs explicit user sign-off before opening a real PR against the live repo** — flag
it and wait rather than doing it autonomously, since it creates visible, external state (a PR,
a workflow run) rather than purely local file changes.

- [ ] **Step 4: Tag and cross-repo consumption test — STOP, user decision required**

Per the spec's Non-Goals, tagging a real release (`v1.0.0`/`v1`) and publishing to the
Marketplace are manual, account-level actions only the user can perform. This plan stops here
and hands back: once Step 3 above is confirmed working, tell the user the action is ready to
tag, and let them decide whether/when to cut `v1.0.0`, move the floating `v1` tag, and publish
to the Marketplace via the GitHub release UI. Do not tag or publish autonomously.
