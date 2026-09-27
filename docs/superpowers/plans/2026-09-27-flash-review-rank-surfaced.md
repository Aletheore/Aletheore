# Flash Review: surface rank (badge, top-issue callout, agent data block) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Surface the ordinal rank that Flash Review's ranking pass (`_rank_findings_with_severity`, PR #793) already computes but never displays: a rank suffix on every inline comment's severity badge, a "top issue" callout at the top of the PR summary comment, and a hidden TOON-encoded data block for an agent to read exact fields from instead of parsing prose.

**Architecture:** Four small, independently-testable changes to `github-app/scan_worker/jobs.py`, in dependency order: (1) `_flash_review_comment_body` gains a rank suffix, (2) `_post_flash_review_finding_comments` starts recording a real, clickable URL onto each finding it leaves visible on the PR (new post, un-resolved, or unchanged), (3) a top-issue selection helper and callout renderer consume that URL in the summary body, (4) a TOON-encoding helper appends a hidden data block. No new job, no new comment, no change to what Flash Review finds, grounds, verifies, or decides to post.

**Tech Stack:** Python 3.12, pytest, `aletheore.toon_encoding.to_toon` (already a dependency of `src/`, already imported elsewhere in this backend — confirm the import path in Task 4).

**Spec:** `docs/superpowers/specs/2026-09-27-pr-comment-presentation-design.md`, Section 3 "Piece A — Rank surfaced on Flash Review" only. Piece B (the standalone "what changed" comment) is out of scope for this plan.

## Global Constraints

- **Presentation only.** No task in this plan changes which findings are generated, grounded, verified, dismissed, or selected for posting. If a step looks like it needs one of those to change, stop and flag it rather than proceed.
- **Same tier gate as today.** Rank/severity only ever exist on a finding when `rank_findings=not is_free_tier` was true (Flash and AIR, never free) — nothing in this plan changes that gate; every new render path must degrade to exactly today's output when `rank`/`severity` are absent.
- **Fail open, exactly like ranking itself.** A malformed, missing, or partially-present `rank`/`severity` must never crash a job or produce garbled output — it must render as if ranking hadn't run at all (this is the existing, tested contract of `_flash_review_comment_body` and `_flash_review_severity_breakdown`; every new piece of code in this plan extends that same contract, never weakens it).
- **No em dashes** in any new user-facing string (matches this codebase's own existing PR-comment copy, which already avoids them).
- **This is a `github-app` backend change**, deployed by hand over SSH per the existing runbook once merged — not a PyPI release, and it has no effect until that deploy happens.
- Real, verified fact this plan relies on: a GitHub PR review comment's real URL is `https://github.com/{repo_full_name}/pull/{pr_number}#discussion_r{comment_id}` — confirmed 2026-09-27 by reading `html_url` off a real comment Aletheore itself posted on `Aletheore/Aletheore#841` (`gh api repos/Aletheore/Aletheore/pulls/841/comments`), not assumed from memory.

## Review Focus

- A finding with `rank` present but `severity` missing (or the reverse) must render with **no** rank suffix and **no** severity prefix — never a badge missing half its information. (Task 1)
- The lowest-ranked finding that actually posted is the one the callout must pick, even when a lower-numbered rank exists among findings that failed to post (a real, already-logged 422 case) — the callout must fall through, not point at a comment that doesn't exist. (Task 3)
- A run where ranking never executed at all (free tier, or ranking failed open this run — no finding anywhere has `rank`/`severity`) must produce a PR summary comment **byte-for-byte identical** to today's: no badge suffix, no callout line, no hidden data block. This is the single highest-risk regression in this plan, since every tier's comment flows through the same code being changed. (Tasks 1, 3, 4)
- `to_toon` can raise `ToonEncodingError` on data it can't round-trip cleanly (documented in its own module, already handled elsewhere in this codebase for `air.toon`). The hidden data block must degrade to being simply absent, never take down the whole summary comment. (Task 4)
- Two findings sharing the same rank number should never happen (the ranking pass validates uniqueness), but the top-issue selection must not crash if it ever did — pick deterministically (lowest rank, first in list order on a tie) rather than raising. (Task 3)

---

## File Structure

- **Modify:** `github-app/scan_worker/jobs.py`
  - `_flash_review_comment_body` (currently lines 2314-2333) — gains a `total_ranked` parameter and a rank suffix on the severity prefix.
  - `_post_flash_review_finding_comments` (currently lines 2367-2486) — every branch that leaves a finding's comment visible on the PR (new post, un-resolve, or untouched) now also records that comment's real URL onto the finding dict.
  - New: `_select_top_issue(findings)`, `_top_issue_callout(finding)`, `_flash_review_data_block(findings)` — small, pure helper functions placed next to `_flash_review_severity_breakdown`.
  - `_run_flash_review`'s summary-body assembly (currently around lines 2976-3088) — wires the callout into the `if posted_count:` branch and appends the data block unconditionally, in the same spot the existing blast-radius section is appended.
- **Modify:** `github-app/tests/test_jobs.py` — new tests alongside the existing `test_flash_review_comment_body_*` / `test_flash_review_severity_breakdown_*` tests (currently lines 4237-4338), plus new full-flow tests alongside the existing `run_flash_review_job` integration tests (the pattern starting around line 4030).

No new files. This is deliberately small: four changes to one already-large file the codebase's own CLAUDE.md already flags as a hotspot, none of them restructuring it.

---

### Task 1: Rank suffix on the inline comment badge

**Files:**
- Modify: `github-app/scan_worker/jobs.py:2314-2333` (`_flash_review_comment_body`)
- Test: `github-app/tests/test_jobs.py` (alongside line 4284-4313, the existing severity-prefix tests)

**Interfaces:**
- Consumes: nothing new — reads `finding["rank"]` (an `int`, may be absent) and `finding["severity"]` (a `str`, may be absent), exactly the same finding dict shape every other part of this file already reads.
- Produces: `_flash_review_comment_body(finding: dict, total_ranked: int = 0) -> str` — the default keeps every existing call site and existing test (which call it with one positional argument) working unchanged.

- [ ] **Step 1: Write the failing tests**

Add these next to the existing severity-prefix tests in `github-app/tests/test_jobs.py`:

```python
def test_flash_review_comment_body_suffixes_rank_when_present_with_severity():
    from scan_worker.jobs import _flash_review_comment_body

    body = _flash_review_comment_body(
        {"file": "app.py", "line": 12, "issue": "real problem", "severity": "High", "rank": 2},
        total_ranked=5,
    )
    assert body.startswith("🟠 **High · #2 of 5**\n\nreal problem")


def test_flash_review_comment_body_omits_rank_suffix_when_rank_absent():
    from scan_worker.jobs import _flash_review_comment_body

    body = _flash_review_comment_body(
        {"file": "app.py", "line": 12, "issue": "real problem", "severity": "High"}, total_ranked=5
    )
    assert body.startswith("🟠 **High**\n\nreal problem")
    assert "#" not in body.split("\n\n")[0]


def test_flash_review_comment_body_omits_rank_suffix_when_severity_absent():
    # Ranking is one call that returns rank+severity together; a finding
    # somehow carrying rank without severity (e.g. a future partial-failure
    # shape) must render exactly like "ranking never ran", never a bare
    # rank with no colour/label around it.
    from scan_worker.jobs import _flash_review_comment_body

    body = _flash_review_comment_body(
        {"file": "app.py", "line": 12, "issue": "real problem", "rank": 2}, total_ranked=5
    )
    assert body.startswith("real problem")


def test_flash_review_comment_body_omits_rank_suffix_when_rank_is_not_a_real_int():
    # bool is a subclass of int in Python - True/False must not slip through
    # isinstance(rank, int) and render as "#1"/"#0".
    from scan_worker.jobs import _flash_review_comment_body

    body = _flash_review_comment_body(
        {"file": "app.py", "line": 12, "issue": "real problem", "severity": "High", "rank": True},
        total_ranked=5,
    )
    assert body.startswith("🟠 **High**\n\nreal problem")


def test_flash_review_comment_body_omits_rank_suffix_when_total_ranked_is_stale():
    # total_ranked is the count from THIS run; a rank higher than it would
    # mean stale/inconsistent data, not a real "#7 of 3" a reader would trust.
    from scan_worker.jobs import _flash_review_comment_body

    body = _flash_review_comment_body(
        {"file": "app.py", "line": 12, "issue": "real problem", "severity": "High", "rank": 7},
        total_ranked=3,
    )
    assert body.startswith("🟠 **High**\n\nreal problem")
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `cd github-app && python -m pytest tests/test_jobs.py -k "comment_body_suffixes_rank or comment_body_omits_rank" -v`
Expected: FAIL (`_flash_review_comment_body() got an unexpected keyword argument 'total_ranked'`, or an assertion mismatch since the suffix doesn't exist yet).

- [ ] **Step 3: Implement**

Replace the current `_flash_review_comment_body` (`github-app/scan_worker/jobs.py:2314-2333`):

```python
def _flash_review_comment_body(finding: dict, total_ranked: int = 0) -> str:
    symbol = finding.get("symbol")
    header = f"**`{symbol}`**\n\n{finding['issue']}" if symbol else finding["issue"]
    severity = finding.get("severity")
    if severity in _SEVERITY_EMOJI:
        rank = finding.get("rank")
        # rank is only trustworthy alongside its own severity (they come from
        # the same ranking call) and only within this run's own total - never
        # a bare number a reader has no way to make sense of.
        has_real_rank = (
            isinstance(rank, int) and not isinstance(rank, bool) and 1 <= rank <= total_ranked
        )
        label = f"{severity} · #{rank} of {total_ranked}" if has_real_rank else severity
        header = f"{_SEVERITY_EMOJI[severity]} **{label}**\n\n{header}"
    lines = [header]
    suggestion = finding.get("suggestion")
    if suggestion:
        fence = "```suggestion" if finding.get("suggestion_clickable") else "```"
        lines.append(f"{fence}\n{suggestion}\n```")
    lines.append(
        "\n_Reply `/dismiss` (optionally with a reason) if this isn't helpful - Aletheore won't "
        "raise it again on this repo._"
    )
    return "\n\n".join(lines)
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `cd github-app && python -m pytest tests/test_jobs.py -k "comment_body" -v`
Expected: PASS — all `test_flash_review_comment_body_*` tests, old and new (this includes re-running the 6 pre-existing ones to confirm the default `total_ranked=0` didn't break them).

- [ ] **Step 5: Commit**

```bash
git add github-app/scan_worker/jobs.py github-app/tests/test_jobs.py
git commit -m "feat: surface Flash Review's computed rank on the inline comment badge"
```

---

### Task 2: Record each finding's real, visible comment URL

**Files:**
- Modify: `github-app/scan_worker/jobs.py:2367-2486` (`_post_flash_review_finding_comments`)
- Test: `github-app/tests/test_jobs.py` (new tests near the existing full-flow `run_flash_review_job` tests, the pattern starting around line 4030)

**Interfaces:**
- Consumes: `_post_flash_review_finding_comments`'s existing 8 parameters, unchanged; `_flash_review_comment_body` from Task 1 (this task is what actually wires `total_ranked` through to both of its real call sites — Task 1 only proved the parameter works in isolation).
- Produces: after this function returns, every finding dict in `findings_to_post` that has a comment currently visible on the PR (freshly posted, un-resolved-and-restored, or left untouched) carries `finding["comment_url"]`, a real GitHub URL string built from the verified format in Global Constraints. A finding whose new post failed (the existing `failed_new_posts` path) has **no** `comment_url` key at all. Return type stays `int` (the existing `failed_new_posts` count) — unchanged. Every comment this function posts or edits now also carries the correct rank suffix, computed once from `findings_to_post` itself (no new return type or extra parameter needed to broaden this, either — see the note below on why this plan diverges from the spec's suggested approach).

**Why not broaden the return type, as the spec's Section 3 suggested:** the spec's stated goal was "the caller needs a comment id per successfully-posted finding" — the means it proposed (a new return shape) isn't the only way to get there. `_post_flash_review_finding_comments` already receives the exact same `findings_to_post` list its caller holds, by reference; mutating each finding dict in place is simpler, touches no other call site (there is exactly one), and doesn't change this function's contract for anyone reading its signature. Both `create_pr_review_comment`'s response (new posts) and the `existing` map's own `row["github_comment_id"]` (un-resolved and untouched cases) already carry the numeric id this needs.

- [ ] **Step 1: Write the failing test**

```python
def test_post_flash_review_finding_comments_records_a_real_url_for_a_new_post(monkeypatch):
    from scan_worker.jobs import _post_flash_review_finding_comments

    monkeypatch.setattr("scan_worker.jobs.get_flash_review_finding_comments", lambda *a, **k: {})
    monkeypatch.setattr(
        "scan_worker.jobs.create_pr_review_comment",
        lambda client, token, repo, pr, commit_id, path, line, body: {"id": 555001},
    )
    monkeypatch.setattr("scan_worker.jobs.insert_flash_review_finding_comment", lambda *a, **k: None)
    finding = {"file": "app.py", "line": 1, "issue": "real problem", "source": "llm"}

    failed = _post_flash_review_finding_comments(
        settings=None, client=None, token="t", installation_id=1,
        repo_full_name="octocat/hello-world", pr_number=42, head_sha="bbb",
        findings_to_post=[finding],
    )

    assert failed == 0
    assert finding["comment_url"] == "https://github.com/octocat/hello-world/pull/42#discussion_r555001"


def test_post_flash_review_finding_comments_renders_the_rank_suffix_on_the_real_posted_body(monkeypatch):
    # The gap a unit test of _flash_review_comment_body in isolation (Task 1)
    # cannot catch: this function is the one real caller that must actually
    # compute and pass total_ranked through, or every deployed comment would
    # show a severity badge with no rank suffix at all, silently.
    from scan_worker.jobs import _post_flash_review_finding_comments

    monkeypatch.setattr("scan_worker.jobs.get_flash_review_finding_comments", lambda *a, **k: {})
    posted_bodies = []
    monkeypatch.setattr(
        "scan_worker.jobs.create_pr_review_comment",
        lambda client, token, repo, pr, commit_id, path, line, body: posted_bodies.append(body)
        or {"id": 1},
    )
    monkeypatch.setattr("scan_worker.jobs.insert_flash_review_finding_comment", lambda *a, **k: None)
    findings = [
        {"file": "a.py", "line": 1, "issue": "x", "source": "llm", "rank": 1, "severity": "High"},
        {"file": "b.py", "line": 2, "issue": "y", "source": "llm", "rank": 2, "severity": "Low"},
    ]

    _post_flash_review_finding_comments(
        settings=None, client=None, token="t", installation_id=1,
        repo_full_name="octocat/hello-world", pr_number=42, head_sha="bbb",
        findings_to_post=findings,
    )

    assert "High · #1 of 2" in posted_bodies[0]
    assert "Low · #2 of 2" in posted_bodies[1]


def test_post_flash_review_finding_comments_omits_url_when_the_post_fails(monkeypatch):
    from scan_worker.jobs import _post_flash_review_finding_comments

    monkeypatch.setattr("scan_worker.jobs.get_flash_review_finding_comments", lambda *a, **k: {})

    def boom(*a, **k):
        raise RuntimeError("GitHub rejected the citation")

    monkeypatch.setattr("scan_worker.jobs.create_pr_review_comment", boom)
    finding = {"file": "app.py", "line": 1, "issue": "real problem", "source": "llm"}

    failed = _post_flash_review_finding_comments(
        settings=None, client=None, token="t", installation_id=1,
        repo_full_name="octocat/hello-world", pr_number=42, head_sha="bbb",
        findings_to_post=[finding],
    )

    assert failed == 1
    assert "comment_url" not in finding


def test_post_flash_review_finding_comments_records_url_for_an_untouched_existing_finding(monkeypatch):
    # The "else: touch_flash_review_finding_comment(...)" branch - a finding
    # already tracked, not un-resolved, not newly posted - still has a real,
    # currently-visible comment; its URL comes from the tracked row's own id.
    from scan_worker.jobs import _post_flash_review_finding_comments

    monkeypatch.setattr(
        "scan_worker.jobs.get_flash_review_finding_comments",
        lambda *a, **k: {
            ("llm", "some-identity-key"): {
                "id": 1, "github_comment_id": 777001, "resolved_at": None,
            }
        },
    )
    monkeypatch.setattr("scan_worker.jobs.finding_identity_key", lambda *a, **k: "some-identity-key")
    monkeypatch.setattr("scan_worker.jobs.touch_flash_review_finding_comment", lambda *a, **k: None)
    finding = {"file": "app.py", "line": 1, "issue": "real problem", "source": "llm"}

    _post_flash_review_finding_comments(
        settings=None, client=None, token="t", installation_id=1,
        repo_full_name="octocat/hello-world", pr_number=42, head_sha="bbb",
        findings_to_post=[finding],
    )

    assert finding["comment_url"] == "https://github.com/octocat/hello-world/pull/42#discussion_r777001"
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `cd github-app && python -m pytest tests/test_jobs.py -k "post_flash_review_finding_comments_records or post_flash_review_finding_comments_omits" -v`
Expected: FAIL (`KeyError: 'comment_url'` or similar — the field isn't written yet).

- [ ] **Step 3: Implement**

In `github-app/scan_worker/jobs.py`, add a small helper right above `_post_flash_review_finding_comments` and use it at the three points inside that function where a finding's comment is confirmed to exist and be current:

```python
def _pr_review_comment_url(repo_full_name: str, pr_number: int, comment_id: int) -> str:
    # Verified 2026-09-27 against a real comment Aletheore posted on its own
    # PR #841 (gh api repos/Aletheore/Aletheore/pulls/841/comments | .html_url) -
    # not assumed from memory or GitHub's general docs.
    return f"https://github.com/{repo_full_name}/pull/{pr_number}#discussion_r{comment_id}"
```

Then, in `_post_flash_review_finding_comments`, right after `existing = get_flash_review_finding_comments(...)` and before the `for finding in findings_to_post:` loop, compute the rank total once:

```python
    total_ranked = sum(
        1 for f in findings_to_post
        if isinstance(f.get("rank"), int) and not isinstance(f.get("rank"), bool)
        and f.get("severity") in _SEVERITY_EMOJI
    )
```

Then, inside the loop:

1. In the `if row is None:` branch (new post), change the existing `create_pr_review_comment(...)` call to pass `total_ranked`, and record the URL right after it succeeds (i.e. inside the `try`, after the `except` block, before `insert_flash_review_finding_comment`):
   ```python
   comment = create_pr_review_comment(
       client, token, repo_full_name, pr_number, head_sha,
       finding["file"], finding["line"], _flash_review_comment_body(finding, total_ranked),
   )
   ```
   ```python
   finding["comment_url"] = _pr_review_comment_url(repo_full_name, pr_number, comment["id"])
   ```
2. In the `elif row["resolved_at"] is not None:` branch (un-resolve), change the existing `edit_pr_review_comment(...)` call the same way, and record the URL right after it, before `touch_flash_review_finding_comment`:
   ```python
   edit_pr_review_comment(
       client, token, repo_full_name, row["github_comment_id"],
       _flash_review_comment_body(finding, total_ranked),
   )
   ```
   ```python
   finding["comment_url"] = _pr_review_comment_url(repo_full_name, pr_number, row["github_comment_id"])
   ```
   (Set the URL regardless of whether the edit itself succeeded — the comment already existed before this run and is still visible even if editing it back to the un-resolved body failed. This branch already wraps its `edit_pr_review_comment` call in its own `try/except`, unchanged by this task.)
3. In the final `else:` branch (untouched, no re-render — this branch never called `_flash_review_comment_body` before this task and still doesn't, since nothing about the comment's body needs to change when a finding is simply confirmed still present), right before `touch_flash_review_finding_comment(dsn, row["id"], head_sha)`:
   ```python
   finding["comment_url"] = _pr_review_comment_url(repo_full_name, pr_number, row["github_comment_id"])
   ```

Do not add `comment_url` anywhere in the loop below this one (the "presumed fixed, mark resolved" loop) — those findings are no longer in `findings_to_post` at all, so there's no finding dict left to attach anything to.

- [ ] **Step 4: Run the tests to verify they pass**

Run: `cd github-app && python -m pytest tests/test_jobs.py -k "post_flash_review_finding_comments" -v`
Expected: PASS — the 3 new tests, plus every pre-existing test in this file that exercises `_post_flash_review_finding_comments` indirectly through `run_flash_review_job` (run the whole file to catch these: `python -m pytest tests/test_jobs.py -v` and confirm no regressions).

- [ ] **Step 5: Commit**

```bash
git add github-app/scan_worker/jobs.py github-app/tests/test_jobs.py
git commit -m "feat: record a real, clickable URL for every finding still visible on the PR"
```

---

### Task 3: Top-issue callout in the summary comment

**Files:**
- Modify: `github-app/scan_worker/jobs.py` (new helpers near `_flash_review_severity_breakdown`, currently line 2339; the summary-body assembly inside `_run_flash_review`, currently around lines 2991-3008)
- Test: `github-app/tests/test_jobs.py`

**Interfaces:**
- Consumes: `finding["rank"]`, `finding["severity"]`, `finding["comment_url"]` (from Task 2), `finding["issue"]` — every field already present by this point in `_run_flash_review`.
- Produces: `_select_top_issue(findings: list[dict]) -> dict | None` and `_top_issue_callout(finding: dict) -> str`. The summary body's `if posted_count:` branch prepends `_top_issue_callout(...)` (when `_select_top_issue` returns something) as its first line.

- [ ] **Step 1: Write the failing tests**

```python
def test_select_top_issue_picks_the_lowest_rank_among_posted_findings():
    from scan_worker.jobs import _select_top_issue

    findings = [
        {"rank": 3, "severity": "Low", "comment_url": "url-3", "issue": "c"},
        {"rank": 1, "severity": "Critical", "comment_url": "url-1", "issue": "a"},
        {"rank": 2, "severity": "High", "comment_url": "url-2", "issue": "b"},
    ]
    top = _select_top_issue(findings)
    assert top["issue"] == "a"


def test_select_top_issue_skips_a_lower_rank_that_never_posted():
    # The real fallback case: rank 1 exists but has no comment_url (its post
    # failed - Task 2 never set the key), so rank 2 is the top issue a reader
    # can actually see.
    from scan_worker.jobs import _select_top_issue

    findings = [
        {"rank": 1, "severity": "Critical", "issue": "never visible"},
        {"rank": 2, "severity": "High", "comment_url": "url-2", "issue": "visible"},
    ]
    top = _select_top_issue(findings)
    assert top["issue"] == "visible"


def test_select_top_issue_returns_none_when_nothing_is_ranked_and_posted():
    from scan_worker.jobs import _select_top_issue

    assert _select_top_issue([{"issue": "a"}, {"issue": "b", "comment_url": "url"}]) is None
    assert _select_top_issue([]) is None


def test_select_top_issue_is_deterministic_on_a_duplicate_rank():
    # Should never happen (the ranking pass validates uniqueness), but this
    # must not crash if it ever did - first in list order wins on a tie.
    from scan_worker.jobs import _select_top_issue

    findings = [
        {"rank": 1, "severity": "High", "comment_url": "url-a", "issue": "first"},
        {"rank": 1, "severity": "High", "comment_url": "url-b", "issue": "second"},
    ]
    assert _select_top_issue(findings)["issue"] == "first"


def test_top_issue_callout_uses_the_findings_own_severity_emoji_and_links_to_it():
    from scan_worker.jobs import _top_issue_callout

    callout = _top_issue_callout(
        {"severity": "Medium", "comment_url": "https://example/pull/1#discussion_r1", "issue": "a real bug"}
    )
    assert callout.startswith("🟡 **Top issue**")
    assert "a real bug" in callout
    assert "https://example/pull/1#discussion_r1" in callout


def test_top_issue_callout_truncates_a_long_multiline_issue_to_one_short_line():
    from scan_worker.jobs import _top_issue_callout

    long_issue = ("x" * 300) + "\nsecond line never shown"
    callout = _top_issue_callout(
        {"severity": "High", "comment_url": "url", "issue": long_issue}
    )
    assert "second line" not in callout
    assert "…" in callout


def test_flash_review_job_summary_leads_with_the_top_issue_callout(monkeypatch):
    # Full-flow: two findings, both ranked, both post successfully - the
    # summary's very first line after the marker/heading is the callout for
    # the rank-1 finding, followed by the existing "N finding(s) posted" line.
    monkeypatch.setenv("DATABASE_URL", "postgresql://unused")
    monkeypatch.setattr("scan_worker.jobs._evidence_by_head_sha_or_none", lambda *a, **k: None)
    monkeypatch.setattr("scan_worker.jobs._latest_evidence_or_none", lambda *a, **k: None)
    monkeypatch.setattr("scan_worker.jobs.get_installation_row", lambda *a, **k: {"plan": "air"})
    monkeypatch.setattr("scan_worker.jobs.check_and_reserve_flash_review_attempt", lambda *a, **k: True)
    monkeypatch.setattr("scan_worker.jobs.check_and_reserve_monthly_repo_scan_slot", lambda *a, **k: True)
    monkeypatch.setattr("scan_worker.jobs.get_llm_spend_this_month", lambda *a, **k: 0.0)
    monkeypatch.setattr("scan_worker.jobs.get_extra_seats", lambda *a, **k: 0)
    monkeypatch.setattr("scan_worker.jobs.get_flash_review_count_this_month", lambda *a, **k: 0)
    monkeypatch.setattr("scan_worker.jobs.get_installation_token", lambda *a, **k: "fake-token")
    monkeypatch.setattr("scan_worker.jobs.generate_app_jwt", lambda *a, **k: "fake-jwt")
    monkeypatch.setattr("scan_worker.jobs.installation_spend_lock", _noop_spend_lock)
    monkeypatch.setattr("scan_worker.jobs.get_last_reviewed_sha", lambda *a, **k: None)
    monkeypatch.setattr("scan_worker.jobs.fetch_pr_diff", lambda *a, **k: "--- app.py ---\n+bug")
    monkeypatch.setattr("scan_worker.jobs.fetch_pr_changed_files", lambda *a, **k: ["app.py"])
    monkeypatch.setattr("scan_worker.jobs.fetch_review_file_context", lambda *a, **k: {})
    monkeypatch.setattr(
        "scan_worker.jobs.review_diff",
        lambda diff_text, file_context="", **kwargs: [
            {"file": "app.py", "line": 1, "issue": "the real bug", "source": "llm",
             "rank": 1, "severity": "Critical"},
            {"file": "app.py", "line": 2, "issue": "a smaller nit", "source": "llm",
             "rank": 2, "severity": "Low"},
        ],
    )
    monkeypatch.setattr("scan_worker.jobs.record_llm_spend", lambda *a, **k: None)
    monkeypatch.setattr("scan_worker.jobs.reserve_flash_review_count", lambda *a, **k: True)
    monkeypatch.setattr("scan_worker.jobs.reserve_llm_spend", lambda *a, **k: True)
    monkeypatch.setattr("scan_worker.jobs.release_flash_review_count_reservation", lambda *a, **k: None)
    monkeypatch.setattr("scan_worker.jobs.release_llm_spend_reservation", lambda *a, **k: None)
    monkeypatch.setattr("scan_worker.jobs.set_last_reviewed_sha", lambda *a, **k: None)
    monkeypatch.setattr("scan_worker.jobs.get_dismissed_identity_keys",
        lambda *a, **k: {"flash_review_llm": set(), "flash_review_semantic": set()})
    monkeypatch.setattr("scan_worker.jobs.get_flash_review_finding_comments", lambda *a, **k: {})
    comment_ids = iter([9001, 9002])
    monkeypatch.setattr(
        "scan_worker.jobs.create_pr_review_comment",
        lambda client, token, repo, pr, commit_id, path, line, body: {"id": next(comment_ids)},
    )
    monkeypatch.setattr("scan_worker.jobs.insert_flash_review_finding_comment", lambda *a, **k: None)
    monkeypatch.setattr("scan_worker.jobs.touch_flash_review_finding_comment", lambda *a, **k: None)
    monkeypatch.setattr("scan_worker.jobs.mark_flash_review_finding_comment_resolved", lambda *a, **k: False)
    monkeypatch.setattr("scan_worker.jobs.insert_review_history", lambda *a, **k: None)
    posted = {}
    monkeypatch.setattr(
        "scan_worker.jobs.upsert_pr_comment",
        lambda client, token, repo_full_name, pr_number, body, **kwargs: posted.update(body=body),
    )
    from scan_worker.jobs import run_flash_review_job

    run_flash_review_job(1, "octocat/hello-world", 42, "aaa", "bbb")

    body = posted["body"]
    callout_line, rest = body.split("\n\n### Aletheore Flash review\n\n")[1].split("\n\n", 1)
    assert "the real bug" in callout_line
    assert "discussion_r9001" in callout_line
    assert rest.startswith("2 finding(s) posted as inline review comment(s) below")
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `cd github-app && python -m pytest tests/test_jobs.py -k "select_top_issue or top_issue_callout or leads_with_the_top_issue" -v`
Expected: FAIL (`ImportError: cannot import name '_select_top_issue'`, then assertion failures once that's fixed).

- [ ] **Step 3: Implement**

Add these next to `_flash_review_severity_breakdown` (`github-app/scan_worker/jobs.py`, after line ~2360):

```python
def _select_top_issue(findings: list[dict]) -> dict | None:
    """The single finding to call out at the top of the summary: the lowest
    rank among findings that are actually visible on the PR right now
    (comment_url set - see _post_flash_review_finding_comments). A finding
    that failed to post has no comment_url and is never eligible, even if
    its rank is lower than everything that did post - pointing a reader at
    a comment that doesn't exist would be worse than no callout at all.
    """
    candidates = [
        f for f in findings
        if isinstance(f.get("rank"), int)
        and not isinstance(f.get("rank"), bool)
        and f.get("severity") in _SEVERITY_EMOJI
        and f.get("comment_url")
    ]
    if not candidates:
        return None
    return min(enumerate(candidates), key=lambda pair: (pair[1]["rank"], pair[0]))[1]


_TOP_ISSUE_TEXT_CAP = 240


def _top_issue_callout(finding: dict) -> str:
    first_line = finding["issue"].split("\n", 1)[0]
    if len(first_line) > _TOP_ISSUE_TEXT_CAP:
        first_line = first_line[:_TOP_ISSUE_TEXT_CAP].rstrip() + "…"
    emoji = _SEVERITY_EMOJI[finding["severity"]]
    return f"{emoji} **Top issue** ({finding['severity']}): {first_line} ([view]({finding['comment_url']}))"
```

Then, in `_run_flash_review`'s `if posted_count:` branch (currently `github-app/scan_worker/jobs.py`, the block building `body` around line 3006-3009), prepend the callout:

```python
    if posted_count:
        suffix = "" if not failed_new_posts else (
            f" ({failed_new_posts} more finding(s) held up but couldn't be posted "
            "as an inline comment - see the job log for the real error.)"
        )
        breakdown = _flash_review_severity_breakdown(findings_to_post)
        breakdown_suffix = f" {breakdown}" if breakdown else ""
        top_issue = _select_top_issue(findings_to_post)
        callout_prefix = f"{_top_issue_callout(top_issue)}\n\n" if top_issue else ""
        body = (
            f"{FLASH_REVIEW_MARKER}\n### Aletheore Flash review\n\n"
            f"{callout_prefix}"
            f"{posted_count} finding(s) posted as inline review comment(s) below.{suffix}{breakdown_suffix}"
        )
```

(Every other branch — the "held up but none could post", "already dismissed", "rejected by verification", "no issues found" branches — is untouched: a callout only makes sense when at least one finding is actually visible on the PR, which is exactly the condition `posted_count` already tests for.)

- [ ] **Step 4: Run the tests to verify they pass**

Run: `cd github-app && python -m pytest tests/test_jobs.py -v`
Expected: PASS — the new tests, and no regressions in any pre-existing `run_flash_review_job` / summary-comment test (several of those assert the body starts with an exact string right after the marker/heading; re-run the full file, not just the new tests, to catch any that now need their assertion adjusted for the case where no finding is ranked, i.e. `top_issue` is `None` and `callout_prefix` is `""` — the body should be byte-for-byte what it was before this task for every one of those un-ranked-finding tests).

- [ ] **Step 5: Commit**

```bash
git add github-app/scan_worker/jobs.py github-app/tests/test_jobs.py
git commit -m "feat: lead the Flash Review summary with a top-issue callout"
```

---

### Task 4: Hidden TOON data block for agents

**Files:**
- Modify: `github-app/scan_worker/jobs.py` (new helper near the Task 3 helpers; the summary-body assembly's tail, currently around lines 3084-3086 where the blast-radius section is appended)
- Test: `github-app/tests/test_jobs.py`

**Interfaces:**
- Consumes: `aletheore.toon_encoding.to_toon` and `aletheore.toon_encoding.ToonEncodingError` (import at the top of `jobs.py`, alongside the existing `from aletheore.evidence import write_evidence` line).
- Produces: `_flash_review_data_block(findings: list[dict]) -> str` — either the full `<!-- aletheore-flash-review-data ... -->` block, or `""`.

- [ ] **Step 1: Write the failing tests**

```python
def test_flash_review_data_block_encodes_every_ranked_finding():
    from scan_worker.jobs import _flash_review_data_block
    import toon

    findings = [
        {"rank": 1, "severity": "High", "file": "app.py", "line": 3, "issue": "a real bug",
         "comment_url": "url-1"},
        {"rank": 2, "severity": "Low", "file": "app.py", "line": 9, "issue": "a nit",
         "comment_url": "url-2"},
    ]
    block = _flash_review_data_block(findings)

    assert block.startswith("\n\n<!-- aletheore-flash-review-data\n")
    assert block.rstrip().endswith("-->")
    inner = block.split("aletheore-flash-review-data\n", 1)[1].rsplit("\n-->", 1)[0]
    decoded = toon.decode(inner)
    assert decoded == [
        {"rank": 1, "severity": "High", "file": "app.py", "line": 3, "issue": "a real bug"},
        {"rank": 2, "severity": "Low", "file": "app.py", "line": 9, "issue": "a nit"},
    ]


def test_flash_review_data_block_empty_when_nothing_is_ranked():
    from scan_worker.jobs import _flash_review_data_block

    assert _flash_review_data_block([{"file": "app.py", "line": 1, "issue": "x"}]) == ""
    assert _flash_review_data_block([]) == ""


def test_flash_review_data_block_skips_a_finding_missing_rank_or_severity_but_keeps_the_rest():
    from scan_worker.jobs import _flash_review_data_block
    import toon

    findings = [
        {"rank": 1, "severity": "High", "file": "a.py", "line": 1, "issue": "ranked", "comment_url": "u"},
        {"file": "b.py", "line": 2, "issue": "unranked, e.g. free tier or ranking failed open"},
    ]
    block = _flash_review_data_block(findings)
    inner = block.split("aletheore-flash-review-data\n", 1)[1].rsplit("\n-->", 1)[0]
    decoded = toon.decode(inner)
    assert len(decoded) == 1
    assert decoded[0]["issue"] == "ranked"


def test_flash_review_data_block_degrades_to_empty_when_toon_encoding_fails(monkeypatch):
    from scan_worker import jobs as jobs_module

    def boom(_data):
        raise jobs_module.ToonEncodingError("pathological shape")

    monkeypatch.setattr(jobs_module, "to_toon", boom)
    findings = [{"rank": 1, "severity": "High", "file": "a.py", "line": 1, "issue": "x", "comment_url": "u"}]

    assert jobs_module._flash_review_data_block(findings) == ""
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `cd github-app && python -m pytest tests/test_jobs.py -k "flash_review_data_block" -v`
Expected: FAIL (`ImportError: cannot import name '_flash_review_data_block'`).

- [ ] **Step 3: Implement**

Add the import near the top of `github-app/scan_worker/jobs.py`, alongside the other `aletheore.*` imports (next to `from aletheore.evidence import write_evidence`):

```python
from aletheore.toon_encoding import ToonEncodingError, to_toon
```

Add the helper near `_flash_review_data_block`'s sibling helpers from Task 3:

```python
def _flash_review_data_block(findings: list[dict]) -> str:
    """A hidden, TOON-encoded copy of every ranked finding's key fields, for
    an agent reviewing (not authoring) this PR to read exact fields from
    instead of parsing the prose above. Invisible on GitHub - HTML comments
    never render - and under its own marker, distinct from FLASH_REVIEW_MARKER
    (which gates the whole comment's upsert), so a consumer can find this
    block without depending on the rest of the comment's shape.

    Empty string, not a malformed or partial block, whenever there is
    nothing ranked to encode (free tier, or a ranking call that failed open
    this run) or when to_toon itself can't encode the data (see its own
    module for when that happens) - either way, a human reading the visible
    part of the comment is completely unaffected.
    """
    ranked = [
        {
            "rank": f["rank"],
            "severity": f["severity"],
            "file": f["file"],
            "line": f["line"],
            "issue": f["issue"],
        }
        for f in findings
        if isinstance(f.get("rank"), int)
        and not isinstance(f.get("rank"), bool)
        and f.get("severity") in _SEVERITY_EMOJI
    ]
    if not ranked:
        return ""
    try:
        encoded = to_toon(ranked)
    except ToonEncodingError:
        return ""
    return f"\n\n<!-- aletheore-flash-review-data\n{encoded}\n-->"
```

Then, in `_run_flash_review`, append it unconditionally right after the existing blast-radius section (currently `github-app/scan_worker/jobs.py`, around line 3084-3086):

```python
    body += _blast_radius_section_for(
        settings.database_url, installation_id, repo_full_name, head_sha, changed_files
    )
    body += _flash_review_data_block(findings_to_post)
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `cd github-app && python -m pytest tests/test_jobs.py -v`
Expected: PASS — full file, no regressions. In particular re-check any existing test that asserts an exact `posted["body"]` value end-to-end (not just a substring) for a case with no ranked findings — those must be completely unaffected, since `_flash_review_data_block` returns `""` for them.

- [ ] **Step 5: Commit**

```bash
git add github-app/scan_worker/jobs.py github-app/tests/test_jobs.py
git commit -m "feat: append a hidden TOON data block to the Flash Review summary for agents"
```

---

## After all four tasks

Run the full `github-app` test suite once, not just `test_jobs.py` (`cd github-app && python -m pytest`), since `jobs.py` is imported by other test files. Then open one PR covering all four tasks (they're small enough, and dependent enough on each other in order, that four separate PRs would just be four rounds of the same review) titled something like "feat: surface Flash Review's rank (badge, top-issue callout, agent data block)", following the same review/CI/Flash-Review-on-itself/merge process every other PR this session has gone through. This does not deploy anything by itself — say so explicitly in the PR body, and deploy separately once merged, the same way every other `github-app` change this session has been deployed.
