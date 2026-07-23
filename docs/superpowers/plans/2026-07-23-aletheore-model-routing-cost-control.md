# Model Routing / Cost Control (Phase 3) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Stop trusting hardcoded model prices as permanent fact, stop paying for model calls on trivial PR diffs, and extend the AIRview-style cache-and-revalidate pattern to Flash review — while leaving managed audits (the highest-stakes call) completely untouched.

**Architecture:** Three independent, additive pieces built in dependency order. (1) `llm_cost.py`'s pricing dict grows a `verified_at` field per model plus a staleness check — pure data + one new function, no callers change shape. (2) A pre-call gatekeeper in `flash_review.py` skips the model entirely for diffs where every changed file is a lockfile or generated asset — wired at the one call site in `jobs.py`. (3) A new `flash_review_cache` table + `flash_review_cache.py` module mirror the AIRview `evidence_packet_cache` / `packet_cache.py` pair built in the previous phase, but keyed on raw diff text instead of a structured evidence packet, and gated by a new diff-hunk parser that confirms a finding's file+line genuinely appears in the *current* diff before ever serving it — fresh or cached.

**Tech Stack:** Python 3.12, psycopg (sync, scan_worker side), Ollama `nomic-embed-text` embeddings (already running as a Docker Compose service from the prior phase — no new service needed), pytest + pytest-asyncio.

## Global Constraints

- Managed audits (`managed_audit.py`) are explicitly out of scope for every piece in this plan — no caching, no gatekeeper. They are deliberately-triggered full-repo runs with no natural "trivial" case and higher stakes than a per-PR diff comment.
- The pricing registry does no live API scraping — it is a staleness-*awareness* mechanism (a logged warning), not automation. No new dependency.
- `SIMILARITY_THRESHOLD = 0.92` and a 200-row lookback window, matching the precedent set by `evidence_packet_cache` in the prior phase — do not invent a different threshold.
- Every cache lookup and cache write must fail open: any exception is caught, logged at `WARNING`, and treated as a miss/no-op. A caching failure must never break an actual review.
- A cache hit must be re-validated against the *current* diff before being served — never trust stored output blindly. This also closes a real pre-existing gap: `review_diff()`'s current validator never checks that a finding's file/line actually appears in the diff, even for a fresh model call.
- Follow existing file conventions: `scan_worker/db.py` helpers use raw `psycopg`, imported locally inside each function (matches every existing helper in that file); cache modules import their DB helpers by name from `scan_worker.db`, matching `packet_cache.py`.

---

### Task 1: Provider pricing registry

**Files:**
- Modify: `github-app/app_server/llm_cost.py` (full file, currently 20 lines)
- Test: `github-app/tests/test_llm_cost.py` (existing file, add to it)

**Interfaces:**
- Consumes: nothing new.
- Produces: `stale_models(as_of: date | None = None, max_age_days: int = STALE_PRICE_MAX_AGE_DAYS) -> list[str]`, used by no other task in this plan but is the registry's public staleness check. `cost_for_usage()` keeps its exact existing signature and return type (`float`) — only its internal behavior gains a one-time-per-model warning log.

- [ ] **Step 1: Write the failing tests**

Add to `github-app/tests/test_llm_cost.py` (below the existing tests, keep the existing `import pytest` / `from app_server.llm_cost import cost_for_usage, monthly_cap_for_installation` line but extend the import):

```python
from datetime import date

from app_server import llm_cost
from app_server.llm_cost import cost_for_usage, monthly_cap_for_installation, stale_models
```

```python
def test_stale_models_returns_empty_when_all_recently_verified():
    assert stale_models(as_of=date(2026, 7, 24)) == []


def test_stale_models_flags_a_model_past_max_age(monkeypatch):
    monkeypatch.setitem(
        llm_cost.MODEL_RATES_PER_MILLION_USD,
        "deepseek-v4-pro",
        {"input": 0.435, "output": 0.87, "verified_at": "2026-01-01"},
    )

    assert stale_models(as_of=date(2026, 7, 23)) == ["deepseek-v4-pro"]


def test_stale_models_respects_custom_max_age(monkeypatch):
    monkeypatch.setitem(
        llm_cost.MODEL_RATES_PER_MILLION_USD,
        "deepseek-v4-pro",
        {"input": 0.435, "output": 0.87, "verified_at": "2026-07-01"},
    )

    assert stale_models(as_of=date(2026, 7, 23), max_age_days=90) == []
    assert stale_models(as_of=date(2026, 7, 23), max_age_days=10) == ["deepseek-v4-pro"]


def test_cost_for_usage_warns_once_per_model_for_stale_pricing(monkeypatch, caplog):
    monkeypatch.setitem(
        llm_cost.MODEL_RATES_PER_MILLION_USD,
        "deepseek-v4-pro",
        {"input": 0.435, "output": 0.87, "verified_at": "2020-01-01"},
    )
    monkeypatch.setattr(llm_cost, "_warned_stale_models", set())

    with caplog.at_level("WARNING"):
        cost_for_usage("deepseek-v4-pro", 1000, 1000)
        cost_for_usage("deepseek-v4-pro", 1000, 1000)

    stale_warnings = [r for r in caplog.records if "deepseek-v4-pro" in r.message]
    assert len(stale_warnings) == 1


def test_cost_for_usage_does_not_warn_for_freshly_verified_model(monkeypatch, caplog):
    monkeypatch.setattr(llm_cost, "_warned_stale_models", set())

    with caplog.at_level("WARNING"):
        cost_for_usage("deepseek-v4-flash", 1000, 1000)

    assert caplog.records == []
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd github-app && python -m pytest tests/test_llm_cost.py -v`
Expected: the four new tests FAIL with `ImportError: cannot import name 'stale_models'` (or `AttributeError` once that's fixed, for `verified_at` `KeyError`).

- [ ] **Step 3: Rewrite `llm_cost.py`**

Replace the entire contents of `github-app/app_server/llm_cost.py` with:

```python
import logging
from datetime import date

logger = logging.getLogger(__name__)

# Cache-miss, list-price rates only - provider list prices, confirm still
# current before relying on them for real spend accounting. Overestimating
# cost is the safe direction for a hard cap, so when in doubt round up.
# verified_at is the date these numbers were last checked against the
# provider's own pricing page - not a promise the price hasn't moved
# since, just an honest record of how stale it might be.
MODEL_RATES_PER_MILLION_USD = {
    "deepseek-v4-pro": {"input": 0.435, "output": 0.87, "verified_at": "2026-07-23"},
    "deepseek-v4-flash": {"input": 0.14, "output": 0.28, "verified_at": "2026-07-23"},
    "gpt-4o": {"input": 2.50, "output": 10.00, "verified_at": "2026-07-23"},
    "claude-opus-4-8": {"input": 15.00, "output": 75.00, "verified_at": "2026-07-23"},
}

STALE_PRICE_MAX_AGE_DAYS = 90

EXTRA_SEAT_MONTHLY_COST_USD = 2.00

# Warn once per process per model, not once per call - cost_for_usage()
# runs on every token-usage callback, and a real deploy could otherwise
# emit thousands of identical warnings for one stale price.
_warned_stale_models: set[str] = set()


def stale_models(as_of: date | None = None, max_age_days: int = STALE_PRICE_MAX_AGE_DAYS) -> list[str]:
    reference = as_of or date.today()
    stale = []
    for model, rates in MODEL_RATES_PER_MILLION_USD.items():
        verified_at = date.fromisoformat(rates["verified_at"])
        if (reference - verified_at).days > max_age_days:
            stale.append(model)
    return stale


def cost_for_usage(model: str, prompt_tokens: int, completion_tokens: int) -> float:
    rates = MODEL_RATES_PER_MILLION_USD[model]
    if model not in _warned_stale_models and model in stale_models():
        logger.warning(
            "price for %s was last verified on %s, more than %d days ago - "
            "confirm it's still accurate against the provider's pricing page",
            model,
            rates["verified_at"],
            STALE_PRICE_MAX_AGE_DAYS,
        )
        _warned_stale_models.add(model)
    return (prompt_tokens * rates["input"] + completion_tokens * rates["output"]) / 1_000_000


def monthly_cap_for_installation(base_cap_usd: float, extra_seats: int) -> float:
    return base_cap_usd + EXTRA_SEAT_MONTHLY_COST_USD * extra_seats
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd github-app && python -m pytest tests/test_llm_cost.py tests/test_model_tiers.py -v`
Expected: all PASS (including the pre-existing tests — `MODEL_RATES_PER_MILLION_USD["model"]["input"]`/`["output"]` lookups are unchanged in shape, only a new `"verified_at"` key was added, so `cost_for_usage`'s numeric behavior is identical).

- [ ] **Step 5: Commit**

```bash
git add github-app/app_server/llm_cost.py github-app/tests/test_llm_cost.py
git commit -m "feat: track price verification dates and warn on stale pricing"
```

---

### Task 2: Diff-hunk parser and structural finding validation

**Files:**
- Modify: `github-app/scan_worker/flash_review.py` (add functions, change `review_diff`'s final return)
- Test: `github-app/tests/test_flash_review.py` (existing file — 2 existing tests need their diff fixtures updated, new tests added)

**Interfaces:**
- Consumes: nothing new.
- Produces: `_diff_valid_lines(diff_text: str) -> dict[str, set[int]]` and `_validate_findings(findings: list[dict], diff_text: str) -> list[dict]`, both used by Task 6 to apply the identical check to cache-served findings.

This task closes a real existing gap: `review_diff()` already receives `diff_text`, but its current validator (the `valid` loop) only checks the *shape* of each finding (file is a non-empty string, line is an int, issue is a non-empty string, no backtick injection) — it never checks that the file was actually part of this diff, or that the line number falls inside a hunk that's actually present. A model can currently hallucinate a plausible-looking `{"file": "unrelated.py", "line": 9999, ...}` and it sails through untouched. This task fixes that for every call, not just cached ones.

- [ ] **Step 1: Write the failing tests**

Add to `github-app/tests/test_flash_review.py`, near the top (after the existing imports, before the first test):

```python
from scan_worker.flash_review import _diff_valid_lines, _validate_findings
```

Add these new tests anywhere in the file:

```python
def test_diff_valid_lines_maps_added_and_context_lines_by_file():
    diff_text = "--- a.py ---\n@@ -1,2 +1,3 @@\n context\n+added\n context2"

    assert _diff_valid_lines(diff_text) == {"a.py": {1, 2, 3}}


def test_diff_valid_lines_excludes_removed_lines():
    diff_text = "--- a.py ---\n@@ -1,2 +1,1 @@\n-removed\n context"

    assert _diff_valid_lines(diff_text) == {"a.py": {1}}


def test_diff_valid_lines_tracks_multiple_files_separately():
    diff_text = (
        "--- a.py ---\n@@ -1,1 +5,1 @@\n+in a\n\n"
        "--- b.py ---\n@@ -1,1 +10,1 @@\n+in b"
    )

    assert _diff_valid_lines(diff_text) == {"a.py": {5}, "b.py": {10}}


def test_validate_findings_keeps_findings_inside_diff_hunks():
    diff_text = "--- a.py ---\n@@ -1,1 +1,1 @@\n+only line"
    findings = [{"file": "a.py", "line": 1, "issue": "valid"}]

    assert _validate_findings(findings, diff_text) == findings


def test_validate_findings_drops_finding_outside_diff_hunks():
    diff_text = "--- a.py ---\n@@ -1,1 +1,1 @@\n+only line"
    findings = [
        {"file": "a.py", "line": 1, "issue": "valid"},
        {"file": "a.py", "line": 99, "issue": "not in this diff"},
        {"file": "b.py", "line": 1, "issue": "file not in this diff"},
    ]

    assert _validate_findings(findings, diff_text) == [{"file": "a.py", "line": 1, "issue": "valid"}]
```

Now update the two existing tests whose fixtures use placeholder (non-parseable) diff text, since real structural validation will otherwise correctly reject their findings. Replace `test_review_diff_parses_valid_findings` with:

```python
@patch("scan_worker.flash_review.OpenAICompatibleAdapter")
def test_review_diff_parses_valid_findings(mock_adapter_class):
    mock_adapter = MagicMock()
    mock_adapter.simple_completion.return_value = (
        '[{"file": "app.py", "line": 42, "issue": "unclosed file handle, never calls .close()"}]'
    )
    mock_adapter_class.return_value = mock_adapter

    findings = review_diff("--- app.py ---\n@@ -40,1 +42,1 @@\n+f = open('x')")

    assert findings == [
        {"file": "app.py", "line": 42, "issue": "unclosed file handle, never calls .close()"}
    ]
```

Replace `test_review_diff_drops_findings_missing_required_fields` with:

```python
@patch("scan_worker.flash_review.OpenAICompatibleAdapter")
def test_review_diff_drops_findings_missing_required_fields(mock_adapter_class):
    mock_adapter = MagicMock()
    mock_adapter.simple_completion.return_value = (
        '[{"file": "app.py", "issue": "missing a line number"}, '
        '{"file": "b.py", "line": 3, "issue": "this one is valid"}]'
    )
    mock_adapter_class.return_value = mock_adapter

    findings = review_diff("--- b.py ---\n@@ -1,1 +3,1 @@\n+something")

    assert findings == [{"file": "b.py", "line": 3, "issue": "this one is valid"}]
```

Also add a new test proving the gap this task closes is now shut for a *fresh* (non-cached) call:

```python
@patch("scan_worker.flash_review.OpenAICompatibleAdapter")
def test_review_diff_drops_a_hallucinated_finding_outside_the_diff(mock_adapter_class):
    mock_adapter = MagicMock()
    mock_adapter.simple_completion.return_value = (
        '[{"file": "app.py", "line": 42, "issue": "real, inside the diff"}, '
        '{"file": "unrelated.py", "line": 9999, "issue": "hallucinated, not in this diff"}]'
    )
    mock_adapter_class.return_value = mock_adapter

    findings = review_diff("--- app.py ---\n@@ -40,1 +42,1 @@\n+f = open('x')")

    assert findings == [{"file": "app.py", "line": 42, "issue": "real, inside the diff"}]
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd github-app && python -m pytest tests/test_flash_review.py -v`
Expected: the new `_diff_valid_lines`/`_validate_findings` tests FAIL with `ImportError`. The two rewritten tests and the new hallucination test currently PASS against the *old* code (since nothing filters by line yet) but will need to keep passing once the import succeeds — run again after Step 3 to confirm real coverage.

- [ ] **Step 3: Implement in `flash_review.py`**

Add near the top of `github-app/scan_worker/flash_review.py`, after the existing imports:

```python
import re
```

Add these two functions right before `def review_diff(`:

```python
_FILE_MARKER_RE = re.compile(r"^--- (.+) ---$")
_HUNK_HEADER_RE = re.compile(r"^@@ -\d+(?:,\d+)? \+(\d+)(?:,\d+)? @@")


def _diff_valid_lines(diff_text: str) -> dict[str, set[int]]:
    """Maps each file in the diff to the set of new-file line numbers
    actually present in its hunks - both added (+) and unchanged context
    lines are real lines in the current file; removed (-) lines are not.

    fetch_pr_diff() joins each file's section with a blank line ("\\n\\n"
    between "--- file ---" blocks) - a real context line for an empty
    source line is a single space, never a zero-length string, so
    treating zero-length lines as separators (not content) is safe and
    necessary: without it, that join artifact gets miscounted as one
    more valid line at the end of the previous file's last hunk.
    """
    valid_lines: dict[str, set[int]] = {}
    current_file: str | None = None
    current_line: int | None = None
    for line in diff_text.splitlines():
        file_match = _FILE_MARKER_RE.match(line)
        if file_match:
            current_file = file_match.group(1)
            valid_lines.setdefault(current_file, set())
            current_line = None
            continue
        hunk_match = _HUNK_HEADER_RE.match(line)
        if hunk_match:
            current_line = int(hunk_match.group(1))
            continue
        if line == "":
            continue
        if current_file is None or current_line is None:
            continue
        if line.startswith("-"):
            continue
        valid_lines[current_file].add(current_line)
        current_line += 1
    return valid_lines


def _validate_findings(findings: list[dict], diff_text: str) -> list[dict]:
    """Drops any finding whose file+line doesn't actually appear in this
    diff's hunks - applies equally to freshly-generated and cache-served
    findings, so a stale or hallucinated finding never reaches a PR
    comment either way.
    """
    valid_lines = _diff_valid_lines(diff_text)
    return [f for f in findings if f["line"] in valid_lines.get(f["file"], set())]
```

Change `review_diff`'s final line from `return valid` to:

```python
    return _validate_findings(valid, diff_text)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd github-app && python -m pytest tests/test_flash_review.py -v`
Expected: all PASS.

- [ ] **Step 5: Run the full github-app suite to check for regressions**

Run: `cd github-app && python -m pytest -q`
Expected: all PASS (Task 2 only changes `flash_review.py`'s internals and return value; `jobs.py`'s existing `review_diff` mocks in `test_jobs.py` bypass the real function entirely via `monkeypatch.setattr`, so they're unaffected).

- [ ] **Step 6: Commit**

```bash
git add github-app/scan_worker/flash_review.py github-app/tests/test_flash_review.py
git commit -m "feat: validate Flash review findings against actual diff hunks"
```

---

### Task 3: Pre-call gatekeeper for non-substantive diffs

**Files:**
- Modify: `github-app/scan_worker/flash_review.py` (add `is_non_substantive_diff`)
- Modify: `github-app/scan_worker/jobs.py:48,371-397` (import + wire into `run_flash_review_job`)
- Test: `github-app/tests/test_flash_review.py`, `github-app/tests/test_jobs.py`

**Interfaces:**
- Consumes: nothing new.
- Produces: `is_non_substantive_diff(changed_files: list[str]) -> bool`, imported by `jobs.py` in this task and unchanged afterward.

- [ ] **Step 1: Write the failing tests**

Add to `github-app/tests/test_flash_review.py`:

```python
from scan_worker.flash_review import is_non_substantive_diff
```

```python
def test_is_non_substantive_diff_true_for_lockfile_only():
    assert is_non_substantive_diff(["package-lock.json"]) is True
    assert is_non_substantive_diff(["yarn.lock", "poetry.lock"]) is True


def test_is_non_substantive_diff_true_for_generated_paths():
    assert is_non_substantive_diff(["dist/bundle.js", "vendor/lib.min.js"]) is True


def test_is_non_substantive_diff_false_when_any_file_is_substantive():
    assert is_non_substantive_diff(["package-lock.json", "app.py"]) is False


def test_is_non_substantive_diff_false_for_normal_source_files():
    assert is_non_substantive_diff(["app.py", "tests/test_app.py"]) is False


def test_is_non_substantive_diff_false_for_empty_list():
    assert is_non_substantive_diff([]) is False
```

Add to `github-app/tests/test_jobs.py`, near the other `run_flash_review_job` tests (after `test_flash_review_job_skips_when_spend_cap_reached`, before `test_flash_review_job_posts_findings_and_updates_state`):

```python
def test_flash_review_job_skips_model_call_for_lockfile_only_diff(monkeypatch):
    monkeypatch.setenv("DATABASE_URL", "postgresql://unused")
    monkeypatch.setattr(
        "scan_worker.jobs.get_installation_row", lambda *a, **k: {"plan": "indie"}
    )
    monkeypatch.setattr(
        "scan_worker.jobs.check_and_reserve_flash_review_attempt", lambda *a, **k: True
    )
    monkeypatch.setattr("scan_worker.jobs.installation_spend_lock", _noop_spend_lock)
    monkeypatch.setattr("scan_worker.jobs.get_llm_spend_this_month", lambda *a, **k: 0.0)
    monkeypatch.setattr("scan_worker.jobs.get_extra_seats", lambda *a, **k: 0)
    monkeypatch.setattr("scan_worker.jobs.get_installation_token", lambda *a, **k: "fake-token")
    monkeypatch.setattr("scan_worker.jobs.generate_app_jwt", lambda *a, **k: "fake-jwt")
    monkeypatch.setattr("scan_worker.jobs.get_last_reviewed_sha", lambda *a, **k: None)
    monkeypatch.setattr(
        "scan_worker.jobs.fetch_pr_diff", lambda *a, **k: "--- package-lock.json ---\n+huge lockfile diff"
    )
    monkeypatch.setattr("scan_worker.jobs.fetch_pr_changed_files", lambda *a, **k: ["package-lock.json"])
    llm_called = []
    monkeypatch.setattr("scan_worker.jobs.review_diff", lambda *a, **k: llm_called.append(True))
    monkeypatch.setattr("scan_worker.jobs.record_llm_spend", lambda *a, **k: None)
    monkeypatch.setattr("scan_worker.jobs.set_last_reviewed_sha", lambda *a, **k: None)
    posted = {}
    monkeypatch.setattr(
        "scan_worker.jobs.upsert_pr_comment",
        lambda client, token, repo_full_name, pr_number, body, **kwargs: posted.update(body=body),
    )
    from scan_worker.jobs import run_flash_review_job

    run_flash_review_job(1, "octocat/hello-world", 42, "aaa", "bbb")

    assert llm_called == []
    assert "no issues found" in posted["body"].lower()
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd github-app && python -m pytest tests/test_flash_review.py tests/test_jobs.py -v -k "non_substantive or lockfile_only"`
Expected: FAIL with `ImportError: cannot import name 'is_non_substantive_diff'`.

- [ ] **Step 3: Implement `is_non_substantive_diff` in `flash_review.py`**

Add to `github-app/scan_worker/flash_review.py`, right after the `_validate_findings` function added in Task 2:

```python
_NON_SUBSTANTIVE_FILENAMES = {
    "package-lock.json",
    "yarn.lock",
    "pnpm-lock.yaml",
    "poetry.lock",
    "Pipfile.lock",
    "Cargo.lock",
    "Gemfile.lock",
    "composer.lock",
    "uv.lock",
}
_NON_SUBSTANTIVE_PATH_PREFIXES = ("dist/", "build/", "vendor/", "node_modules/")
_NON_SUBSTANTIVE_SUFFIXES = (".min.js", ".min.css")


def _is_non_substantive_path(path: str) -> bool:
    filename = path.rsplit("/", 1)[-1]
    if filename in _NON_SUBSTANTIVE_FILENAMES:
        return True
    if path.startswith(_NON_SUBSTANTIVE_PATH_PREFIXES):
        return True
    if filename.endswith(_NON_SUBSTANTIVE_SUFFIXES):
        return True
    return False


def is_non_substantive_diff(changed_files: list[str]) -> bool:
    """True only when there's at least one changed file and every one of
    them is a lockfile or generated/minified asset - an empty list is
    not itself non-substantive, it just means there's nothing to check.
    """
    return bool(changed_files) and all(_is_non_substantive_path(f) for f in changed_files)
```

- [ ] **Step 4: Wire the gatekeeper into `jobs.py`**

In `github-app/scan_worker/jobs.py`, change line 48 from:

```python
from scan_worker.flash_review import build_code_evidence_context, gather_file_context, review_diff
```

to:

```python
from scan_worker.flash_review import (
    build_code_evidence_context,
    gather_file_context,
    is_non_substantive_diff,
    review_diff,
)
```

Replace lines 371-397 of `run_flash_review_job` (from `last_reviewed_sha = get_last_reviewed_sha(` through `record_llm_spend(settings.database_url, installation_id, spend_accumulator["total"])`) with:

```python
        last_reviewed_sha = get_last_reviewed_sha(
            settings.database_url, installation_id, repo_full_name, pr_number
        )
        diff_base = last_reviewed_sha or base_sha
        diff_text = fetch_pr_diff(client, token, repo_full_name, diff_base, head_sha)
        changed_files = fetch_pr_changed_files(client, token, repo_full_name, diff_base, head_sha)

        spend_accumulator = {"total": 0.0}

        if is_non_substantive_diff(changed_files):
            findings: list[dict] = []
        else:
            file_context = gather_file_context(client, token, repo_full_name, changed_files, head_sha)
            evidence = _latest_evidence_or_none(settings.database_url, installation_id, repo_full_name)
            code_evidence_context = build_code_evidence_context(evidence, changed_files)

            def _on_usage(prompt_tokens: int, completion_tokens: int) -> None:
                spend_accumulator["total"] += cost_for_usage(
                    "deepseek-v4-flash", prompt_tokens, completion_tokens
                )

            if code_evidence_context:
                findings = review_diff(
                    diff_text,
                    file_context=file_context,
                    code_evidence_context=code_evidence_context,
                    on_usage=_on_usage,
                )
            else:
                findings = review_diff(diff_text, file_context=file_context, on_usage=_on_usage)
        record_llm_spend(settings.database_url, installation_id, spend_accumulator["total"])
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `cd github-app && python -m pytest tests/test_flash_review.py tests/test_jobs.py -v`
Expected: all PASS.

- [ ] **Step 6: Run the full github-app suite to check for regressions**

Run: `cd github-app && python -m pytest -q`
Expected: all PASS.

- [ ] **Step 7: Commit**

```bash
git add github-app/scan_worker/flash_review.py github-app/scan_worker/jobs.py github-app/tests/test_flash_review.py github-app/tests/test_jobs.py
git commit -m "feat: skip Flash review model calls for lockfile/generated-only diffs"
```

---

### Task 4: `flash_review_cache` migration and DB helpers

**Files:**
- Create: `github-app/migrations/013_flash_review_cache.sql`
- Modify: `github-app/scan_worker/db.py` (append new helpers)
- Test: `github-app/tests/test_scan_worker_db.py`

**Interfaces:**
- Consumes: nothing new.
- Produces: `insert_flash_review_cache_row(dsn, installation_id, repo_full_name, content_hash, embedding, diff_text, findings, model_used) -> None`, `list_recent_flash_review_cache_rows(dsn, installation_id, repo_full_name, limit=200) -> list[dict]`, `record_flash_review_cache_hit(dsn, row_id) -> None` — all consumed by Task 5's `flash_review_cache.py`.

- [ ] **Step 1: Write the failing tests**

Add to `github-app/tests/test_scan_worker_db.py`, after the existing `test_list_evidence_packet_cache_rows_never_crosses_installations` test:

```python
@pytest.mark.asyncio
async def test_insert_and_list_flash_review_cache_rows(pool):
    await _insert_installation(pool, 411, "flash-org")

    from scan_worker.db import insert_flash_review_cache_row, list_recent_flash_review_cache_rows

    insert_flash_review_cache_row(
        TEST_DATABASE_URL,
        411,
        "flash-org/repo",
        "hash-1",
        [0.1, 0.2, 0.3],
        "--- a.py ---\n@@ -1,1 +1,1 @@\n+x = 1",
        [{"file": "a.py", "line": 1, "issue": "unused variable"}],
        "deepseek-v4-flash",
    )

    rows = list_recent_flash_review_cache_rows(TEST_DATABASE_URL, 411, "flash-org/repo")

    assert len(rows) == 1
    assert rows[0]["content_hash"] == "hash-1"
    assert rows[0]["embedding"] == [0.1, 0.2, 0.3]
    assert rows[0]["diff_text"] == "--- a.py ---\n@@ -1,1 +1,1 @@\n+x = 1"
    assert rows[0]["findings"] == [{"file": "a.py", "line": 1, "issue": "unused variable"}]
    assert rows[0]["model_used"] == "deepseek-v4-flash"


@pytest.mark.asyncio
async def test_list_flash_review_cache_rows_never_crosses_installations(pool):
    await _insert_installation(pool, 412, "org-a")
    await _insert_installation(pool, 413, "org-b")

    from scan_worker.db import insert_flash_review_cache_row, list_recent_flash_review_cache_rows

    insert_flash_review_cache_row(
        TEST_DATABASE_URL, 412, "org-a/repo", "hash-a", [1.0], "diff a", [], "deepseek-v4-flash"
    )
    insert_flash_review_cache_row(
        TEST_DATABASE_URL, 413, "org-b/repo", "hash-b", [1.0], "diff b", [], "deepseek-v4-flash"
    )

    rows = list_recent_flash_review_cache_rows(TEST_DATABASE_URL, 412, "org-a/repo")

    assert len(rows) == 1
    assert rows[0]["content_hash"] == "hash-a"


@pytest.mark.asyncio
async def test_record_flash_review_cache_hit_updates_hit_count_and_last_hit_at(pool):
    await _insert_installation(pool, 414, "hit-org")

    from scan_worker.db import (
        insert_flash_review_cache_row,
        list_recent_flash_review_cache_rows,
        record_flash_review_cache_hit,
    )

    insert_flash_review_cache_row(
        TEST_DATABASE_URL, 414, "hit-org/repo", "hash-1", [1.0], "diff", [], "deepseek-v4-flash"
    )
    row_id = list_recent_flash_review_cache_rows(TEST_DATABASE_URL, 414, "hit-org/repo")[0]["id"]

    record_flash_review_cache_hit(TEST_DATABASE_URL, row_id)

    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            "SELECT hit_count, last_hit_at FROM flash_review_cache WHERE id = $1", row_id
        )
    assert row["hit_count"] == 1
    assert row["last_hit_at"] is not None
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd github-app && python -m pytest tests/test_scan_worker_db.py -v -k flash_review_cache`
Expected: FAIL — Postgres either isn't reachable locally (tests SKIP, in which case skip ahead to Step 5 and rely on CI) or the table doesn't exist yet (`asyncpg`/`psycopg` error: `relation "flash_review_cache" does not exist`).

- [ ] **Step 3: Create the migration**

Create `github-app/migrations/013_flash_review_cache.sql`:

```sql
-- Per-installation similarity cache for Flash review diff findings.
-- Rows are always queried by installation_id and repo_full_name before any
-- Python-side similarity comparison happens.
CREATE TABLE IF NOT EXISTS flash_review_cache (
    id               BIGSERIAL PRIMARY KEY,
    installation_id  BIGINT NOT NULL REFERENCES installations(installation_id) ON DELETE CASCADE,
    repo_full_name   TEXT NOT NULL,
    content_hash     TEXT NOT NULL,
    embedding        DOUBLE PRECISION[] NOT NULL,
    diff_text        TEXT NOT NULL,
    findings         JSONB NOT NULL,
    model_used       TEXT NOT NULL,
    created_at       TIMESTAMPTZ NOT NULL DEFAULT now(),
    last_hit_at      TIMESTAMPTZ,
    hit_count        INT NOT NULL DEFAULT 0
);

CREATE INDEX IF NOT EXISTS flash_review_cache_lookup
ON flash_review_cache (installation_id, repo_full_name, created_at DESC);
```

- [ ] **Step 4: Add the DB helpers**

Append to the end of `github-app/scan_worker/db.py`:

```python
def insert_flash_review_cache_row(
    dsn: str,
    installation_id: int,
    repo_full_name: str,
    content_hash: str,
    embedding: list[float],
    diff_text: str,
    findings: list[dict],
    model_used: str,
) -> None:
    import psycopg

    with psycopg.connect(dsn) as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO flash_review_cache
                    (installation_id, repo_full_name, content_hash, embedding,
                     diff_text, findings, model_used)
                VALUES (%s, %s, %s, %s, %s, %s::jsonb, %s)
                """,
                (
                    installation_id,
                    repo_full_name,
                    content_hash,
                    embedding,
                    diff_text,
                    json.dumps(findings),
                    model_used,
                ),
            )
        conn.commit()


def list_recent_flash_review_cache_rows(
    dsn: str, installation_id: int, repo_full_name: str, limit: int = 200
) -> list[dict]:
    import psycopg.rows

    with psycopg.connect(dsn) as conn:
        with conn.cursor(row_factory=psycopg.rows.dict_row) as cur:
            cur.execute(
                """
                SELECT id, content_hash, embedding, diff_text, findings, model_used, hit_count
                FROM flash_review_cache
                WHERE installation_id = %s AND repo_full_name = %s
                ORDER BY created_at DESC
                LIMIT %s
                """,
                (installation_id, repo_full_name, limit),
            )
            return cur.fetchall()


def record_flash_review_cache_hit(dsn: str, row_id: int) -> None:
    import psycopg

    with psycopg.connect(dsn) as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                UPDATE flash_review_cache
                SET hit_count = hit_count + 1, last_hit_at = now()
                WHERE id = %s
                """,
                (row_id,),
            )
        conn.commit()
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `cd github-app && python -m pytest tests/test_scan_worker_db.py -v -k flash_review_cache`
Expected: all PASS (or SKIP with "test Postgres unavailable" if no local Postgres — the CI job in `.github/workflows/github-app-tests.yml` runs these against a real Postgres service, so this is confirmed there regardless).

- [ ] **Step 6: Commit**

```bash
git add github-app/migrations/013_flash_review_cache.sql github-app/scan_worker/db.py github-app/tests/test_scan_worker_db.py
git commit -m "feat: add flash_review_cache table and DB helpers"
```

---

### Task 5: `flash_review_cache.py` similarity module

**Files:**
- Create: `github-app/scan_worker/flash_review_cache.py`
- Test: `github-app/tests/test_flash_review_cache.py`

**Interfaces:**
- Consumes: `insert_flash_review_cache_row`, `list_recent_flash_review_cache_rows`, `record_flash_review_cache_hit` from Task 4's `scan_worker.db`; `embed_text(text: str, base_url=None, timeout_seconds=5.0) -> list[float] | None` from the existing `scan_worker.embedding_client` (built in the prior phase — no changes needed there, it's already generic).
- Produces: `lookup_cached_result(dsn: str, installation_id: int, repo_full_name: str, diff_text: str) -> list[dict] | None` and `store_result(dsn: str, installation_id: int, repo_full_name: str, diff_text: str, findings: list[dict], model_used: str) -> None`, both consumed by Task 6's `jobs.py` wiring.

- [ ] **Step 1: Write the failing tests**

Create `github-app/tests/test_flash_review_cache.py`:

```python
import pytest

from scan_worker.flash_review_cache import lookup_cached_result, store_result


def test_lookup_returns_none_when_embedding_unavailable(monkeypatch):
    monkeypatch.setattr("scan_worker.flash_review_cache.embed_text", lambda text: None)

    result = lookup_cached_result("postgresql://unused", 1, "org/repo", "some diff")

    assert result is None


def test_lookup_returns_none_when_no_rows_exist(monkeypatch):
    monkeypatch.setattr("scan_worker.flash_review_cache.embed_text", lambda text: [1.0, 0.0])
    monkeypatch.setattr(
        "scan_worker.flash_review_cache.list_recent_flash_review_cache_rows", lambda *a, **k: []
    )

    result = lookup_cached_result("postgresql://unused", 1, "org/repo", "some diff")

    assert result is None


def test_lookup_returns_none_below_similarity_threshold(monkeypatch):
    monkeypatch.setattr("scan_worker.flash_review_cache.embed_text", lambda text: [1.0, 0.0])
    monkeypatch.setattr(
        "scan_worker.flash_review_cache.list_recent_flash_review_cache_rows",
        lambda *a, **k: [{"id": 1, "embedding": [0.0, 1.0], "findings": [], "model_used": "deepseek-v4-flash"}],
    )

    result = lookup_cached_result("postgresql://unused", 1, "org/repo", "some diff")

    assert result is None


def test_lookup_returns_findings_above_threshold_and_records_hit(monkeypatch):
    monkeypatch.setattr("scan_worker.flash_review_cache.embed_text", lambda text: [1.0, 0.0])
    monkeypatch.setattr(
        "scan_worker.flash_review_cache.list_recent_flash_review_cache_rows",
        lambda *a, **k: [
            {
                "id": 7,
                "embedding": [1.0, 0.0001],
                "findings": [{"file": "a.py", "line": 1, "issue": "cached finding"}],
                "model_used": "deepseek-v4-flash",
            }
        ],
    )
    recorded = []
    monkeypatch.setattr(
        "scan_worker.flash_review_cache.record_flash_review_cache_hit",
        lambda dsn, row_id: recorded.append(row_id),
    )

    result = lookup_cached_result("postgresql://unused", 1, "org/repo", "some diff")

    assert result == [{"file": "a.py", "line": 1, "issue": "cached finding"}]
    assert recorded == [7]


def test_lookup_fails_open_when_db_lookup_raises(monkeypatch):
    monkeypatch.setattr("scan_worker.flash_review_cache.embed_text", lambda text: [1.0, 0.0])

    def _raise(*a, **k):
        raise RuntimeError("db unavailable")

    monkeypatch.setattr("scan_worker.flash_review_cache.list_recent_flash_review_cache_rows", _raise)

    result = lookup_cached_result("postgresql://unused", 1, "org/repo", "some diff")

    assert result is None


def test_store_result_writes_a_row(monkeypatch):
    written = {}

    def fake_insert(dsn, installation_id, repo_full_name, content_hash, embedding, diff_text, findings, model_used):
        written.update(
            installation_id=installation_id,
            repo_full_name=repo_full_name,
            content_hash=content_hash,
            embedding=embedding,
            diff_text=diff_text,
            findings=findings,
            model_used=model_used,
        )

    monkeypatch.setattr("scan_worker.flash_review_cache.embed_text", lambda text: [0.5, 0.5])
    monkeypatch.setattr("scan_worker.flash_review_cache.insert_flash_review_cache_row", fake_insert)

    store_result(
        "postgresql://unused",
        1,
        "org/repo",
        "--- a.py ---\n@@ -1,1 +1,1 @@\n+x = 1",
        [{"file": "a.py", "line": 1, "issue": "fresh finding"}],
        "deepseek-v4-flash",
    )

    assert written["installation_id"] == 1
    assert written["repo_full_name"] == "org/repo"
    assert written["embedding"] == [0.5, 0.5]
    assert written["findings"] == [{"file": "a.py", "line": 1, "issue": "fresh finding"}]
    assert written["model_used"] == "deepseek-v4-flash"


def test_store_result_is_noop_when_embedding_unavailable(monkeypatch):
    called = []
    monkeypatch.setattr("scan_worker.flash_review_cache.embed_text", lambda text: None)
    monkeypatch.setattr(
        "scan_worker.flash_review_cache.insert_flash_review_cache_row", lambda *a, **k: called.append(True)
    )

    store_result("postgresql://unused", 1, "org/repo", "diff", [], "deepseek-v4-flash")

    assert called == []


def test_store_result_fails_open_when_insert_raises(monkeypatch):
    monkeypatch.setattr("scan_worker.flash_review_cache.embed_text", lambda text: [0.5, 0.5])

    def _raise(*a, **k):
        raise RuntimeError("db unavailable")

    monkeypatch.setattr("scan_worker.flash_review_cache.insert_flash_review_cache_row", _raise)

    # Must not raise.
    store_result("postgresql://unused", 1, "org/repo", "diff", [], "deepseek-v4-flash")


@pytest.mark.asyncio
async def test_lookup_never_returns_a_different_installations_row(pool, monkeypatch):
    from conftest import TEST_DATABASE_URL

    await pool.execute(
        "INSERT INTO installations (installation_id, account_login) VALUES ($1, $2)",
        601,
        "org-a",
    )
    await pool.execute(
        "INSERT INTO installations (installation_id, account_login) VALUES ($1, $2)",
        602,
        "org-b",
    )

    monkeypatch.setattr("scan_worker.flash_review_cache.embed_text", lambda text: [1.0, 0.0])

    store_result(
        TEST_DATABASE_URL,
        601,
        "org-a/repo",
        "diff for org-a",
        [{"file": "a.py", "line": 1, "issue": "org-a's cached finding"}],
        "deepseek-v4-flash",
    )

    result = lookup_cached_result(TEST_DATABASE_URL, 602, "org-b/repo", "diff for org-a")

    assert result is None
```

`test_flash_review_cache.py` now has exactly: the 8 mock-based tests above, followed by this one real `pool`-based tenant-isolation test — nothing else.

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd github-app && python -m pytest tests/test_flash_review_cache.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'scan_worker.flash_review_cache'`.

- [ ] **Step 3: Implement `flash_review_cache.py`**

Create `github-app/scan_worker/flash_review_cache.py`:

```python
"""Per-installation similarity cache for Flash review diff findings.

Callers must re-validate cached findings against the current diff's
actual hunks before serving them (see flash_review._validate_findings).
This module only finds similar past diffs and stores raw model output.
"""

import hashlib
import logging
import math

from scan_worker.db import (
    insert_flash_review_cache_row,
    list_recent_flash_review_cache_rows,
    record_flash_review_cache_hit,
)
from scan_worker.embedding_client import embed_text

SIMILARITY_THRESHOLD = 0.92

logger = logging.getLogger(__name__)


def _content_hash(text: str) -> str:
    return hashlib.sha256(text.encode()).hexdigest()


def _cosine_similarity(a: list[float], b: list[float]) -> float:
    if len(a) != len(b):
        return 0.0
    dot = sum(x * y for x, y in zip(a, b))
    norm_a = math.sqrt(sum(x * x for x in a))
    norm_b = math.sqrt(sum(y * y for y in b))
    if norm_a == 0 or norm_b == 0:
        return 0.0
    return dot / (norm_a * norm_b)


def lookup_cached_result(
    dsn: str, installation_id: int, repo_full_name: str, diff_text: str
) -> list[dict] | None:
    try:
        vector = embed_text(diff_text)
        if vector is None:
            return None

        rows = list_recent_flash_review_cache_rows(dsn, installation_id, repo_full_name)
        if not rows:
            return None

        best_row = None
        best_score = 0.0
        for row in rows:
            score = _cosine_similarity(vector, row["embedding"])
            if score > best_score:
                best_score = score
                best_row = row

        if best_row is None or best_score < SIMILARITY_THRESHOLD:
            return None

        record_flash_review_cache_hit(dsn, best_row["id"])
        return best_row["findings"]
    except Exception as exc:
        logger.warning("flash review cache lookup failed (%s); treating as miss", type(exc).__name__)
        return None


def store_result(
    dsn: str,
    installation_id: int,
    repo_full_name: str,
    diff_text: str,
    findings: list[dict],
    model_used: str,
) -> None:
    try:
        vector = embed_text(diff_text)
        if vector is None:
            logger.warning("embedding unavailable; skipping flash review cache write")
            return

        insert_flash_review_cache_row(
            dsn,
            installation_id,
            repo_full_name,
            _content_hash(diff_text),
            vector,
            diff_text,
            findings,
            model_used,
        )
    except Exception as exc:
        logger.warning("flash review cache write failed (%s); continuing without cache", type(exc).__name__)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd github-app && python -m pytest tests/test_flash_review_cache.py -v`
Expected: all PASS (the `pool`-based tenant-isolation test PASSes if local Postgres is available, else SKIPs — confirmed in CI regardless).

- [ ] **Step 5: Commit**

```bash
git add github-app/scan_worker/flash_review_cache.py github-app/tests/test_flash_review_cache.py
git commit -m "feat: add similarity cache for Flash review findings"
```

---

### Task 6: Wire caching into `review_diff` and `run_flash_review_job`

**Files:**
- Modify: `github-app/scan_worker/flash_review.py` (add `cache_lookup`/`cache_write`/`model_used` params to `review_diff`)
- Modify: `github-app/scan_worker/jobs.py` (import + wire cache callables)
- Test: `github-app/tests/test_flash_review.py`, `github-app/tests/test_jobs.py`

**Interfaces:**
- Consumes: `lookup_cached_result`/`store_result` from Task 5's `scan_worker.flash_review_cache`; `_validate_findings` from Task 2.
- Produces: `review_diff`'s final signature — `review_diff(diff_text: str, file_context: str = "", code_evidence_context: str = "", on_usage: Callable[[int, int], None] | None = None, *, cache_lookup: Callable[[str], list[dict] | None] | None = None, cache_write: Callable[[str, list[dict], str], None] | None = None, model_used: str = "deepseek-v4-flash") -> list[dict]`.

- [ ] **Step 1: Write the failing tests**

Add to `github-app/tests/test_flash_review.py`:

```python
def test_review_diff_serves_validated_cache_hit_without_calling_the_model(monkeypatch):
    diff_text = "--- app.py ---\n@@ -40,1 +42,1 @@\n+f = open('x')"
    cached_findings = [{"file": "app.py", "line": 42, "issue": "cached finding"}]

    with patch("scan_worker.flash_review.OpenAICompatibleAdapter") as mock_adapter_class:
        findings = review_diff(diff_text, cache_lookup=lambda diff: cached_findings)

    mock_adapter_class.assert_not_called()
    assert findings == cached_findings


def test_review_diff_revalidates_cache_hit_against_current_diff():
    diff_text = "--- app.py ---\n@@ -40,1 +42,1 @@\n+f = open('x')"
    cached_findings = [
        {"file": "app.py", "line": 42, "issue": "still valid"},
        {"file": "app.py", "line": 9999, "issue": "stale - not in this diff anymore"},
    ]

    with patch("scan_worker.flash_review.OpenAICompatibleAdapter") as mock_adapter_class:
        findings = review_diff(diff_text, cache_lookup=lambda diff: cached_findings)

    mock_adapter_class.assert_not_called()
    assert findings == [{"file": "app.py", "line": 42, "issue": "still valid"}]


@patch("scan_worker.flash_review.OpenAICompatibleAdapter")
def test_review_diff_falls_through_to_model_call_on_cache_miss(mock_adapter_class):
    mock_adapter = MagicMock()
    mock_adapter.simple_completion.return_value = (
        '[{"file": "app.py", "line": 42, "issue": "fresh finding"}]'
    )
    mock_adapter_class.return_value = mock_adapter
    diff_text = "--- app.py ---\n@@ -40,1 +42,1 @@\n+f = open('x')"

    findings = review_diff(diff_text, cache_lookup=lambda diff: None)

    assert findings == [{"file": "app.py", "line": 42, "issue": "fresh finding"}]


@patch("scan_worker.flash_review.OpenAICompatibleAdapter")
def test_review_diff_writes_to_cache_after_a_fresh_call(mock_adapter_class):
    mock_adapter = MagicMock()
    mock_adapter.simple_completion.return_value = (
        '[{"file": "app.py", "line": 42, "issue": "fresh finding"}]'
    )
    mock_adapter_class.return_value = mock_adapter
    diff_text = "--- app.py ---\n@@ -40,1 +42,1 @@\n+f = open('x')"
    written = []

    review_diff(
        diff_text,
        cache_lookup=lambda diff: None,
        cache_write=lambda diff, findings, model_used: written.append((diff, findings, model_used)),
        model_used="deepseek-v4-flash",
    )

    assert written == [
        (diff_text, [{"file": "app.py", "line": 42, "issue": "fresh finding"}], "deepseek-v4-flash")
    ]


@patch("scan_worker.flash_review.OpenAICompatibleAdapter")
def test_review_diff_does_not_call_the_model_at_all_for_an_empty_diff_even_with_cache_lookup(
    mock_adapter_class,
):
    cache_lookup_called = []

    findings = review_diff("", cache_lookup=lambda diff: cache_lookup_called.append(True))

    assert findings == []
    assert cache_lookup_called == []
    mock_adapter_class.assert_not_called()
```

Now update the three `run_flash_review_job` tests in `github-app/tests/test_jobs.py` whose `review_diff` mock lambdas have a fixed signature that the new `cache_lookup`/`cache_write`/`model_used` keyword arguments would break. In `test_flash_review_job_posts_findings_and_updates_state`, change:

```python
    monkeypatch.setattr(
        "scan_worker.jobs.review_diff",
        lambda diff_text, file_context="", on_usage=None: [
            {"file": "app.py", "line": 1, "issue": "real problem"}
        ],
    )
```

to:

```python
    monkeypatch.setattr(
        "scan_worker.jobs.review_diff",
        lambda diff_text, file_context="", **kwargs: [
            {"file": "app.py", "line": 1, "issue": "real problem"}
        ],
    )
```

In `test_flash_review_job_renders_suggestion_as_plain_fence_not_github_suggestion_syntax`, change:

```python
    monkeypatch.setattr(
        "scan_worker.jobs.review_diff",
        lambda diff_text, file_context="", on_usage=None: [
            {"file": "app.py", "line": 1, "issue": "unclosed handle", "suggestion": "f.close()"}
        ],
    )
```

to:

```python
    monkeypatch.setattr(
        "scan_worker.jobs.review_diff",
        lambda diff_text, file_context="", **kwargs: [
            {"file": "app.py", "line": 1, "issue": "unclosed handle", "suggestion": "f.close()"}
        ],
    )
```

In `test_flash_review_job_posts_no_issues_found_when_findings_empty`, change:

```python
    monkeypatch.setattr("scan_worker.jobs.review_diff", lambda diff_text, file_context="", on_usage=None: [])
```

to:

```python
    monkeypatch.setattr("scan_worker.jobs.review_diff", lambda diff_text, file_context="", **kwargs: [])
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd github-app && python -m pytest tests/test_flash_review.py -v -k cache`
Expected: FAIL — `review_diff()` doesn't yet accept `cache_lookup`/`cache_write`/`model_used` (`TypeError: review_diff() got an unexpected keyword argument 'cache_lookup'`).

- [ ] **Step 3: Update `review_diff` in `flash_review.py`**

Replace the full `review_diff` function in `github-app/scan_worker/flash_review.py` with:

```python
def review_diff(
    diff_text: str,
    file_context: str = "",
    code_evidence_context: str = "",
    on_usage: Callable[[int, int], None] | None = None,
    *,
    cache_lookup: Callable[[str], list[dict] | None] | None = None,
    cache_write: Callable[[str, list[dict], str], None] | None = None,
    model_used: str = "deepseek-v4-flash",
) -> list[dict]:
    if not diff_text.strip():
        return []

    if cache_lookup is not None:
        try:
            cached = cache_lookup(diff_text)
        except Exception as exc:
            logger.warning("flash review cache lookup failed (%s); treating as miss", type(exc).__name__)
            cached = None
        if cached is not None:
            return _validate_findings(cached, diff_text)

    adapter = OpenAICompatibleAdapter(
        name="DeepSeek",
        base_url="https://api.deepseek.com",
        api_key_env_var="DEEPSEEK_API_KEY",
        model="deepseek-v4-flash",
        on_usage=on_usage,
    )
    prompt_parts = [diff_text]
    if file_context:
        prompt_parts.append(file_context)
    if code_evidence_context:
        prompt_parts.append(code_evidence_context)
    user_prompt = "\n\n".join(prompt_parts)
    raw_output = adapter.simple_completion(FLASH_REVIEW_SYSTEM_PROMPT, user_prompt, cwd=".")

    try:
        findings = json.loads(raw_output)
    except json.JSONDecodeError:
        return []

    if not isinstance(findings, list):
        return []

    valid: list[dict] = []
    for finding in findings:
        if not (
            isinstance(finding, dict)
            and isinstance(finding.get("file"), str)
            and finding.get("file")
            and isinstance(finding.get("line"), int)
            and isinstance(finding.get("issue"), str)
            and finding.get("issue")
        ):
            continue
        # "issue" is rendered into the PR comment with no fence at all (see
        # jobs.py) - a triple-backtick sequence there could break out and
        # inject a real ```suggestion block, which GitHub renders as a
        # one-click-apply code change. Drop the whole finding rather than
        # try to escape it: legitimate issue text never needs a code fence.
        if "```" in finding["issue"]:
            continue
        result = {"file": finding["file"], "line": finding["line"], "issue": finding["issue"]}
        suggestion = finding.get("suggestion")
        if isinstance(suggestion, str) and suggestion.strip() and "```" not in suggestion:
            result["suggestion"] = suggestion.strip()
        valid.append(result)

    if cache_write is not None:
        try:
            cache_write(diff_text, valid, model_used)
        except Exception as exc:
            logger.warning("flash review cache write failed (%s); continuing without cache", type(exc).__name__)

    return _validate_findings(valid, diff_text)
```

Add `logging` to the imports at the top of `github-app/scan_worker/flash_review.py` (needed for the new `logger.warning` calls) and a module logger, right after the existing `import re` added in Task 2:

```python
import logging
```

and, near the top of the file after the imports block (before `FLASH_REVIEW_SYSTEM_PROMPT`):

```python
logger = logging.getLogger(__name__)
```

- [ ] **Step 4: Wire cache callables into `jobs.py`**

In `github-app/scan_worker/jobs.py`, change the import added in Task 3 from:

```python
from scan_worker.flash_review import (
    build_code_evidence_context,
    gather_file_context,
    is_non_substantive_diff,
    review_diff,
)
```

to also import the Task 5 cache functions under aliases (jobs.py already imports `lookup_cached_result`/`store_result` from `scan_worker.packet_cache` for AIRview caching at line 52 — these names would collide):

```python
from scan_worker.flash_review import (
    build_code_evidence_context,
    gather_file_context,
    is_non_substantive_diff,
    review_diff,
)
from scan_worker.flash_review_cache import (
    lookup_cached_result as lookup_cached_flash_review_result,
    store_result as store_flash_review_result,
)
```

Add this import line right after the existing `from scan_worker.flash_review import (...)` block and before `from scan_worker.github_api import ...` (keeping the alphabetical-ish grouping already in the file).

Replace the `else` branch of the `if is_non_substantive_diff(changed_files):` block (written in Task 3) — everything from `file_context = gather_file_context(...)` through the `findings = review_diff(diff_text, file_context=file_context, on_usage=_on_usage)` line — with:

```python
        else:
            file_context = gather_file_context(client, token, repo_full_name, changed_files, head_sha)
            evidence = _latest_evidence_or_none(settings.database_url, installation_id, repo_full_name)
            code_evidence_context = build_code_evidence_context(evidence, changed_files)
            dsn = settings.database_url

            def _on_usage(prompt_tokens: int, completion_tokens: int) -> None:
                spend_accumulator["total"] += cost_for_usage(
                    "deepseek-v4-flash", prompt_tokens, completion_tokens
                )

            def _cache_lookup(diff: str) -> list[dict] | None:
                return lookup_cached_flash_review_result(dsn, installation_id, repo_full_name, diff)

            def _cache_write(diff: str, found: list[dict], used: str) -> None:
                store_flash_review_result(dsn, installation_id, repo_full_name, diff, found, used)

            if code_evidence_context:
                findings = review_diff(
                    diff_text,
                    file_context=file_context,
                    code_evidence_context=code_evidence_context,
                    on_usage=_on_usage,
                    cache_lookup=_cache_lookup,
                    cache_write=_cache_write,
                    model_used="deepseek-v4-flash",
                )
            else:
                findings = review_diff(
                    diff_text,
                    file_context=file_context,
                    on_usage=_on_usage,
                    cache_lookup=_cache_lookup,
                    cache_write=_cache_write,
                    model_used="deepseek-v4-flash",
                )
```

The full `run_flash_review_job` body from `last_reviewed_sha = get_last_reviewed_sha(` through this `else` block should now read:

```python
        last_reviewed_sha = get_last_reviewed_sha(
            settings.database_url, installation_id, repo_full_name, pr_number
        )
        diff_base = last_reviewed_sha or base_sha
        diff_text = fetch_pr_diff(client, token, repo_full_name, diff_base, head_sha)
        changed_files = fetch_pr_changed_files(client, token, repo_full_name, diff_base, head_sha)

        spend_accumulator = {"total": 0.0}

        if is_non_substantive_diff(changed_files):
            findings: list[dict] = []
        else:
            file_context = gather_file_context(client, token, repo_full_name, changed_files, head_sha)
            evidence = _latest_evidence_or_none(settings.database_url, installation_id, repo_full_name)
            code_evidence_context = build_code_evidence_context(evidence, changed_files)
            dsn = settings.database_url

            def _on_usage(prompt_tokens: int, completion_tokens: int) -> None:
                spend_accumulator["total"] += cost_for_usage(
                    "deepseek-v4-flash", prompt_tokens, completion_tokens
                )

            def _cache_lookup(diff: str) -> list[dict] | None:
                return lookup_cached_flash_review_result(dsn, installation_id, repo_full_name, diff)

            def _cache_write(diff: str, found: list[dict], used: str) -> None:
                store_flash_review_result(dsn, installation_id, repo_full_name, diff, found, used)

            if code_evidence_context:
                findings = review_diff(
                    diff_text,
                    file_context=file_context,
                    code_evidence_context=code_evidence_context,
                    on_usage=_on_usage,
                    cache_lookup=_cache_lookup,
                    cache_write=_cache_write,
                    model_used="deepseek-v4-flash",
                )
            else:
                findings = review_diff(
                    diff_text,
                    file_context=file_context,
                    on_usage=_on_usage,
                    cache_lookup=_cache_lookup,
                    cache_write=_cache_write,
                    model_used="deepseek-v4-flash",
                )
        record_llm_spend(settings.database_url, installation_id, spend_accumulator["total"])
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `cd github-app && python -m pytest tests/test_flash_review.py tests/test_jobs.py -v`
Expected: all PASS.

- [ ] **Step 6: Run the full github-app and prototype suites to check for regressions**

Run: `cd github-app && python -m pytest -q && cd ../prototype && python -m pytest -q`
Expected: all PASS.

- [ ] **Step 7: Validate Docker Compose config still parses**

Run: `cd github-app && POSTGRES_PASSWORD=x docker compose config >/dev/null && echo "compose config OK"`
Expected: `compose config OK` — no changes to `docker-compose.yml` in this plan, the existing `ollama` service from the prior phase is reused as-is; this step is a final sanity check that nothing else in this task's edits broke the compose file (it shouldn't have, since this task only touches Python files).

- [ ] **Step 8: Commit**

```bash
git add github-app/scan_worker/flash_review.py github-app/scan_worker/jobs.py github-app/tests/test_flash_review.py github-app/tests/test_jobs.py
git commit -m "feat: wire similarity caching into Flash review"
```

---

## Self-Review

**Spec coverage:**
- Provider pricing registry with `verified_at` + staleness warning → Task 1. ✅
- Pre-call gatekeeper for Flash review only, not managed audits → Task 3 (Task 1/2 confirm managed audits are never touched — no task modifies `managed_audit.py`). ✅
- Semantic result caching for Flash review, audits untouched → Tasks 4-6. ✅
- Dual re-verification (cache hit gets the same validation a fresh call would) → Task 2 builds `_validate_findings`, Task 6 applies it identically on both the `cache_lookup` hit path (`return _validate_findings(cached, diff_text)`) and the fresh-call path (`return _validate_findings(valid, diff_text)`). ✅
- Fail-open caching (lookup/write failures never break a review) → `flash_review_cache.py`'s try/except in Task 5, plus `review_diff`'s own try/except around `cache_lookup(...)` in Task 6 (a `flash_review_cache` bug can't crash `review_diff` even if `flash_review_cache.py`'s own internal try/except somehow didn't catch it). ✅
- 0.92 similarity threshold, 200-row window, reuse of existing `embedding_client.py` and Ollama service → Task 5 (`SIMILARITY_THRESHOLD = 0.92`, `limit: int = 200` default in Task 4's `list_recent_flash_review_cache_rows`), no Docker Compose changes needed since the `ollama` service already exists from the prior phase. ✅
- Tenant isolation regression test → Task 5's `test_lookup_never_returns_a_different_installations_row`. ✅

**Placeholder scan:** No "TBD"/"TODO"/broken fragments in any task — an earlier draft of Task 5 Step 1 had a stray broken import left in as a "tripwire," which was itself a placeholder-scan violation; removed and replaced with the complete, correct test file content.

**Type consistency:** `review_diff`'s `cache_lookup: Callable[[str], list[dict] | None]` (Task 6) matches `flash_review_cache.lookup_cached_result`'s return type `list[dict] | None` (Task 5). `cache_write: Callable[[str, list[dict], str], None]` matches `store_result(dsn, installation_id, repo_full_name, diff_text, findings, model_used)`'s wrapped-lambda shape `_cache_write(diff, found, used)` in Task 6's `jobs.py` wiring. `is_non_substantive_diff(changed_files: list[str]) -> bool` (Task 3) is used consistently in both `flash_review.py` and its one call site in `jobs.py`. `_diff_valid_lines`/`_validate_findings` (Task 2) are used with the same signatures in Task 6's cache-hit path.

**Scope check:** All three pieces (pricing registry, gatekeeper, Flash review caching) are additive and independently testable — Task 1 has zero dependency on Tasks 2-6 and could ship alone; Tasks 2-3 (validation + gatekeeper) are useful on their own even if caching were cut; Tasks 4-6 depend on Task 2's `_validate_findings` but not on Tasks 1 or 3. Managed audits are untouched throughout, matching the user's explicit scope decision.
