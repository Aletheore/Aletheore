# Managed Audit API Signing Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Every managed-audit report gets signed and persisted the same way, regardless of whether it was triggered from a PR comment or the public API - and a DB/signing hiccup never destroys the audit content itself.

**Architecture:** Extract the sign-and-persist logic that `run_managed_audit_pr_job` already has into one shared, fail-open helper (`_sign_and_persist_audit_report`), then call it from both `run_managed_audit_pr_job` (fixing a real gap: today a persistence failure there replaces the entire audit with a generic error comment) and `run_managed_audit_api_job` (which currently signs nothing at all). The API path surfaces its `verification_token` via RQ's `job.meta`, not the job's `result` - `result` stays the plain report-text string it always was, so the CLI's existing `run_managed_audit_request() -> str` contract never breaks.

**Tech Stack:** Python 3.12, `rq` (already a dependency, `get_current_job`/`job.meta`/`save_meta` already used elsewhere in this codebase for exactly this kind of side-channel data), pytest.

## Global Constraints

- `run_managed_audit_api_job`'s return type does not change - it still returns the plain report-text `str`. Verification info is additive, delivered through `job.meta`, never through `result`.
- Signing/persistence must fail open in both call sites: if `insert_audit_report` or `sign_report` raises for any reason, the real audit report still reaches the user - the only thing that's missing is the verify link/token.
- `managed_audit_client.py` (the CLI's HTTP client) is not modified - it's out of scope for this fix and its existing `-> str` contract must keep working unchanged.

---

### Task 1: Extract a shared, fail-open sign-and-persist helper; fix the PR path's fragility

**Files:**
- Modify: `github-app/scan_worker/jobs.py` (add `_sign_and_persist_audit_report`, update `run_managed_audit_pr_job`)
- Test: `github-app/tests/test_jobs.py`

**Interfaces:**
- Consumes: `content_hash`, `sign_report` from `app_server.audit_signing` (already imported); `insert_audit_report` from `scan_worker.db` (already imported); `secrets.token_hex` (already imported).
- Produces: `_sign_and_persist_audit_report(settings, installation_id: int, repo_full_name: str, report_text: str) -> str | None` - returns the verification token on success, `None` on any failure. Consumed by Task 2's `run_managed_audit_api_job`.

- [ ] **Step 1: Write the failing test for fail-open behavior**

The existing `test_managed_audit_pr_job_persists_and_signs_the_report` test (in `github-app/tests/test_jobs.py`) already covers the happy path. Add a new test right after it, proving the actual bug this task fixes - today, a persistence failure destroys the whole audit:

```python
def test_managed_audit_pr_job_still_posts_report_when_signing_fails(monkeypatch, tmp_path):
    work = tmp_path / "work"
    work.mkdir()
    subprocess.run(["git", "init", "-q"], cwd=work, check=True)
    subprocess.run(["git", "config", "user.email", "test@example.com"], cwd=work, check=True)
    subprocess.run(["git", "config", "user.name", "Test"], cwd=work, check=True)
    (work / "app.py").write_text("print('hello')\n")
    subprocess.run(["git", "add", "."], cwd=work, check=True)
    subprocess.run(["git", "commit", "-q", "-m", "commit"], cwd=work, check=True)
    head_sha = subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=work, check=True, capture_output=True, text=True
    ).stdout.strip()
    bare = tmp_path / "bare.git"
    subprocess.run(["git", "clone", "-q", "--bare", str(work), str(bare)], check=True)
    subprocess.run(
        ["git", "--git-dir", str(bare), "update-ref", "refs/pull/42/head", head_sha], check=True
    )

    monkeypatch.setenv("DATABASE_URL", "postgresql://unused")
    monkeypatch.setattr("scan_worker.jobs.get_installation_row", lambda *a, **k: {"plan": "indie"})
    monkeypatch.setattr("scan_worker.jobs._clone_url", lambda repo_full_name, token: str(bare))
    monkeypatch.setattr("scan_worker.jobs.get_installation_token", lambda *a, **k: "fake-token")
    monkeypatch.setattr("scan_worker.jobs.generate_app_jwt", lambda *a, **k: "fake-jwt")
    monkeypatch.setattr("scan_worker.jobs.run_managed_audit", lambda *a, **k: "the audit findings")
    monkeypatch.setattr("scan_worker.jobs.check_and_reserve_managed_audit", lambda *a, **k: True)
    monkeypatch.setattr("scan_worker.jobs.installation_spend_lock", _noop_spend_lock)
    monkeypatch.setattr("scan_worker.jobs.get_llm_spend_this_month", lambda *a, **k: 0.0)
    monkeypatch.setattr("scan_worker.jobs.get_extra_seats", lambda *a, **k: 0)
    monkeypatch.setattr("scan_worker.jobs.record_llm_spend", lambda *a, **k: None)

    def _raise(*a, **k):
        raise RuntimeError("db unavailable")

    monkeypatch.setattr("scan_worker.jobs.insert_audit_report", _raise)
    posted = {}
    monkeypatch.setattr(
        "scan_worker.jobs.upsert_pr_comment",
        lambda client, token, repo_full_name, pr_number, body, **kwargs: posted.update(body=body),
    )

    from scan_worker.jobs import AUDIT_COMMENT_MARKER, run_managed_audit_pr_job

    run_managed_audit_pr_job(1, "octocat/hello-world", 42)

    assert "the audit findings" in posted["body"]
    assert posted["marker"] == AUDIT_COMMENT_MARKER
    assert "Verify this report" not in posted["body"]
```

Note this test asserts `posted["marker"]` but the existing `upsert_pr_comment` mock lambda in this test only captures `body` via `posted.update(body=body)` - extend the lambda to also capture the marker, matching the pattern already used in `test_flash_review_job_posts_findings_and_updates_state` elsewhere in this file:

```python
    monkeypatch.setattr(
        "scan_worker.jobs.upsert_pr_comment",
        lambda client, token, repo_full_name, pr_number, body, **kwargs: posted.update(
            body=body, marker=kwargs.get("marker")
        ),
    )
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd github-app && python -m pytest tests/test_jobs.py -v -k still_posts_report_when_signing_fails`
Expected: FAIL - today, `insert_audit_report` raising propagates all the way to the outer `except Exception as exc:` in `run_managed_audit_pr_job`, which calls `_post_failure_comment` instead - `posted["body"]` will contain "couldn't complete this scan", not "the audit findings".

- [ ] **Step 3: Add the shared helper**

Add to `github-app/scan_worker/jobs.py`, right before `run_managed_audit_pr_job`:

```python
def _sign_and_persist_audit_report(
    settings, installation_id: int, repo_full_name: str, report_text: str
) -> str | None:
    try:
        verification_token = secrets.token_hex(32)
        report_hash = content_hash(report_text)
        signature = sign_report(report_text, settings.audit_signing_private_key)
        insert_audit_report(
            settings.database_url,
            installation_id,
            repo_full_name,
            verification_token,
            report_text,
            report_hash,
            signature,
        )
        return verification_token
    except Exception as exc:  # noqa: BLE001
        logging.getLogger("scan_worker.jobs").warning(
            "audit report signing/persistence failed (%s); report still returned unsigned",
            type(exc).__name__,
        )
        return None
```

- [ ] **Step 4: Use it in `run_managed_audit_pr_job`, fail-open**

Change the block in `run_managed_audit_pr_job` from:

```python
                    verification_token = secrets.token_hex(32)
                    report_hash = content_hash(report_text)
                    signature = sign_report(report_text, settings.audit_signing_private_key)
                    insert_audit_report(
                        settings.database_url,
                        installation_id,
                        repo_full_name,
                        verification_token,
                        report_text,
                        report_hash,
                        signature,
                    )
                    verify_url = f"{settings.public_base_url}/v1/audit/{verification_token}/verify"
                    body = (
                        f"{AUDIT_COMMENT_MARKER}\n### Aletheore managed audit\n\n"
                        f"{report_text}\n\n[Verify this report]({verify_url})"
                    )
```

to:

```python
                    verification_token = _sign_and_persist_audit_report(
                        settings, installation_id, repo_full_name, report_text
                    )
                    if verification_token is not None:
                        verify_url = f"{settings.public_base_url}/v1/audit/{verification_token}/verify"
                        body = (
                            f"{AUDIT_COMMENT_MARKER}\n### Aletheore managed audit\n\n"
                            f"{report_text}\n\n[Verify this report]({verify_url})"
                        )
                    else:
                        body = f"{AUDIT_COMMENT_MARKER}\n### Aletheore managed audit\n\n{report_text}"
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `cd github-app && python -m pytest tests/test_jobs.py -v -k managed_audit_pr_job`
Expected: all PASS, including the pre-existing `test_managed_audit_pr_job_persists_and_signs_the_report` (unaffected - the happy path still produces the same `body` content, just via the extracted helper).

- [ ] **Step 6: Run the full github-app suite to check for regressions**

Run: `cd github-app && python -m pytest -q`
Expected: all PASS.

- [ ] **Step 7: Commit**

```bash
git add github-app/scan_worker/jobs.py github-app/tests/test_jobs.py
git commit -m "fix: make managed-audit report signing fail open instead of eating the report"
```

---

### Task 2: Sign and persist API-triggered audit reports

**Files:**
- Modify: `github-app/scan_worker/jobs.py:429` (`run_managed_audit_api_job`)
- Test: `github-app/tests/test_jobs.py`
- Modify: `github-app/app_server/managed_audit_api.py` (`start_managed_audit`, `get_managed_audit_status`)
- Test: `github-app/tests/test_managed_audit_api.py`

**Interfaces:**
- Consumes: `_sign_and_persist_audit_report` from Task 1.
- Produces: `run_managed_audit_api_job(installation_id: int, evidence: dict | str, repo_full_name: str) -> str` (new required `repo_full_name` parameter; return type unchanged). `GET /v1/managed-audit/{job_id}`'s finished response gains a `"verification_token": str | None` field alongside the existing `"result"`.

- [ ] **Step 1: Write the failing job-level test**

Add to `github-app/tests/test_jobs.py`, right after `test_managed_audit_api_job_returns_report_text`:

```python
def test_managed_audit_api_job_signs_and_persists_the_report(monkeypatch):
    monkeypatch.setattr("scan_worker.jobs.get_installation_row", lambda *a, **k: {"plan": "indie"})
    monkeypatch.setattr("scan_worker.jobs.installation_spend_lock", _noop_spend_lock)
    monkeypatch.setattr("scan_worker.jobs.get_llm_spend_this_month", lambda *a, **k: 0.0)
    monkeypatch.setattr("scan_worker.jobs.get_extra_seats", lambda *a, **k: 0)
    monkeypatch.setattr("scan_worker.jobs.record_llm_spend", lambda *a, **k: None)
    monkeypatch.setattr("scan_worker.jobs.run_managed_audit", lambda *a, **k: "# API Report")
    monkeypatch.setenv("DATABASE_URL", "postgresql://unused")
    stored = {}
    monkeypatch.setattr(
        "scan_worker.jobs.insert_audit_report",
        lambda dsn, iid, repo, token, text, chash, sig: stored.update(
            installation_id=iid, repo_full_name=repo, token=token, text=text
        ),
    )

    from scan_worker.jobs import run_managed_audit_api_job

    result = run_managed_audit_api_job(
        installation_id=100, evidence={"scanned_at": "2026-01-01"}, repo_full_name="octocat/widgets"
    )

    assert "API Report" in result
    assert stored["installation_id"] == 100
    assert stored["repo_full_name"] == "octocat/widgets"
    assert stored["text"] == "# API Report"
    assert len(stored["token"]) == 64


def test_managed_audit_api_job_still_returns_report_when_signing_fails(monkeypatch):
    monkeypatch.setattr("scan_worker.jobs.get_installation_row", lambda *a, **k: {"plan": "indie"})
    monkeypatch.setattr("scan_worker.jobs.installation_spend_lock", _noop_spend_lock)
    monkeypatch.setattr("scan_worker.jobs.get_llm_spend_this_month", lambda *a, **k: 0.0)
    monkeypatch.setattr("scan_worker.jobs.get_extra_seats", lambda *a, **k: 0)
    monkeypatch.setattr("scan_worker.jobs.record_llm_spend", lambda *a, **k: None)
    monkeypatch.setattr("scan_worker.jobs.run_managed_audit", lambda *a, **k: "# API Report")
    monkeypatch.setenv("DATABASE_URL", "postgresql://unused")

    def _raise(*a, **k):
        raise RuntimeError("db unavailable")

    monkeypatch.setattr("scan_worker.jobs.insert_audit_report", _raise)

    from scan_worker.jobs import run_managed_audit_api_job

    result = run_managed_audit_api_job(
        installation_id=100, evidence={"scanned_at": "2026-01-01"}, repo_full_name="octocat/widgets"
    )

    assert "API Report" in result
```

Update the existing `test_managed_audit_api_job_returns_report_text` and `test_managed_audit_api_job_raises_when_spend_cap_reached` calls, which currently omit `repo_full_name` - a now-required parameter. Change both `run_managed_audit_api_job(installation_id=100, evidence={...})` calls to:

```python
    result = run_managed_audit_api_job(
        installation_id=100, evidence={"scanned_at": "2026-01-01"}, repo_full_name="octocat/widgets"
    )
```

and, for the spend-cap test:

```python
    with pytest.raises(Exception, match="spend cap"):
        run_managed_audit_api_job(
            installation_id=100, evidence={"scanned_at": "2026-01-01"}, repo_full_name="octocat/widgets"
        )
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd github-app && python -m pytest tests/test_jobs.py -v -k managed_audit_api_job`
Expected: FAIL - `TypeError: run_managed_audit_api_job() missing 1 required positional argument: 'repo_full_name'` for all of them.

- [ ] **Step 3: Update `run_managed_audit_api_job`**

Change the function signature and body in `github-app/scan_worker/jobs.py` from:

```python
def run_managed_audit_api_job(installation_id: int, evidence: dict | str) -> str:
    settings = get_settings()
    installation = get_installation_row(settings.database_url, installation_id)
    plan = installation["plan"] if installation is not None else "indie"
    with installation_spend_lock(settings.database_url, installation_id):
        extra_seats = get_extra_seats(settings.database_url, installation_id)
        monthly_cap = monthly_cap_for_installation(7.00, extra_seats)
        current_spend = get_llm_spend_this_month(settings.database_url, installation_id)
        if current_spend >= monthly_cap:
            raise RuntimeError(
                f"monthly spend cap reached for this installation (${monthly_cap:.2f})"
            )

        job_dir = _job_temp_dir()
        try:
            if isinstance(evidence, dict):
                write_evidence(evidence, job_dir)
            else:
                aletheore_dir = job_dir / ".aletheore"
                aletheore_dir.mkdir(parents=True, exist_ok=True)
                (aletheore_dir / "air.toon").write_text(evidence)
                (aletheore_dir / "air.json").write_text(json.dumps({"managed_evidence": True}))
            spend_accumulator = {"total": 0.0, "model": model_for_plan(plan)}

            def _on_usage(prompt_tokens: int, completion_tokens: int) -> None:
                spend_accumulator["total"] += cost_for_usage(
                    spend_accumulator["model"], prompt_tokens, completion_tokens
                )

            result = run_managed_audit(job_dir, on_usage=_on_usage, plan=plan)
            record_llm_spend(settings.database_url, installation_id, spend_accumulator["total"])
            return result
        finally:
            shutil.rmtree(job_dir, ignore_errors=True)
```

to:

```python
def run_managed_audit_api_job(installation_id: int, evidence: dict | str, repo_full_name: str) -> str:
    settings = get_settings()
    installation = get_installation_row(settings.database_url, installation_id)
    plan = installation["plan"] if installation is not None else "indie"
    with installation_spend_lock(settings.database_url, installation_id):
        extra_seats = get_extra_seats(settings.database_url, installation_id)
        monthly_cap = monthly_cap_for_installation(7.00, extra_seats)
        current_spend = get_llm_spend_this_month(settings.database_url, installation_id)
        if current_spend >= monthly_cap:
            raise RuntimeError(
                f"monthly spend cap reached for this installation (${monthly_cap:.2f})"
            )

        job_dir = _job_temp_dir()
        try:
            if isinstance(evidence, dict):
                write_evidence(evidence, job_dir)
            else:
                aletheore_dir = job_dir / ".aletheore"
                aletheore_dir.mkdir(parents=True, exist_ok=True)
                (aletheore_dir / "air.toon").write_text(evidence)
                (aletheore_dir / "air.json").write_text(json.dumps({"managed_evidence": True}))
            spend_accumulator = {"total": 0.0, "model": model_for_plan(plan)}

            def _on_usage(prompt_tokens: int, completion_tokens: int) -> None:
                spend_accumulator["total"] += cost_for_usage(
                    spend_accumulator["model"], prompt_tokens, completion_tokens
                )

            result = run_managed_audit(job_dir, on_usage=_on_usage, plan=plan)
            record_llm_spend(settings.database_url, installation_id, spend_accumulator["total"])
            verification_token = _sign_and_persist_audit_report(
                settings, installation_id, repo_full_name, result
            )
            job = get_current_job()
            if job is not None:
                job.meta["verification_token"] = verification_token
                job.save_meta()
            return result
        finally:
            shutil.rmtree(job_dir, ignore_errors=True)
```

Add `from rq import get_current_job` to the imports at the top of `github-app/scan_worker/jobs.py` - place it near the other stdlib/third-party imports, above the `from aletheore...` block.

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd github-app && python -m pytest tests/test_jobs.py -v -k managed_audit_api_job`
Expected: all PASS.

- [ ] **Step 5: Write the failing API-layer tests**

Add to `github-app/tests/test_managed_audit_api.py`, right after `test_managed_audit_rate_limit_is_independent_per_repo`:

```python
@pytest.mark.asyncio
async def test_start_managed_audit_passes_repo_full_name_to_the_job(pool, monkeypatch):
    await upsert_installation(pool, 100, "octocat")
    await set_installation_plan(pool, 100, "indie")
    token_hash = hashlib.sha256(b"real-token").hexdigest()
    await create_api_token(pool, 100, token_hash, "laptop", "octocat")
    fake_queue = MagicMock()
    fake_queue.enqueue.return_value = MagicMock(id="job-123")
    monkeypatch.setattr("app_server.managed_audit_api._get_queue", lambda redis_url: fake_queue)

    app.state.db_pool = pool
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        await client.post(
            "/v1/managed-audit",
            json={"evidence": _evidence_toon(), "repo_full_name": "octocat/widgets"},
            headers={"Authorization": "Bearer real-token"},
        )

    _, kwargs = fake_queue.enqueue.call_args
    assert kwargs["repo_full_name"] == "octocat/widgets"
```

Update `test_get_job_status_returns_result_when_finished`'s `fake_job` and assertion from:

```python
    fake_job = MagicMock(is_finished=True, is_failed=False, result="# Report", kwargs={"installation_id": 100})
    monkeypatch.setattr("app_server.managed_audit_api._fetch_job", lambda job_id, redis_url: fake_job)

    app.state.db_pool = pool
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.get(
            "/v1/managed-audit/job-123", headers={"Authorization": "Bearer real-token"}
        )

    assert response.status_code == 200
    assert response.json() == {"status": "finished", "result": "# Report"}
```

to:

```python
    fake_job = MagicMock(
        is_finished=True, is_failed=False, result="# Report", kwargs={"installation_id": 100}, meta={}
    )
    monkeypatch.setattr("app_server.managed_audit_api._fetch_job", lambda job_id, redis_url: fake_job)

    app.state.db_pool = pool
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.get(
            "/v1/managed-audit/job-123", headers={"Authorization": "Bearer real-token"}
        )

    assert response.status_code == 200
    assert response.json() == {"status": "finished", "result": "# Report", "verification_token": None}
```

Add a new test right after it, proving the token surfaces when present:

```python
@pytest.mark.asyncio
async def test_get_job_status_includes_verification_token_when_present(pool, monkeypatch):
    await upsert_installation(pool, 100, "octocat")
    await set_installation_plan(pool, 100, "indie")
    token_hash = hashlib.sha256(b"real-token").hexdigest()
    await create_api_token(pool, 100, token_hash, "laptop", "octocat")

    fake_job = MagicMock(
        is_finished=True,
        is_failed=False,
        result="# Report",
        kwargs={"installation_id": 100},
        meta={"verification_token": "abc123"},
    )
    monkeypatch.setattr("app_server.managed_audit_api._fetch_job", lambda job_id, redis_url: fake_job)

    app.state.db_pool = pool
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.get(
            "/v1/managed-audit/job-123", headers={"Authorization": "Bearer real-token"}
        )

    assert response.status_code == 200
    assert response.json() == {"status": "finished", "result": "# Report", "verification_token": "abc123"}
```

- [ ] **Step 6: Run tests to verify they fail**

Run: `cd github-app && python -m pytest tests/test_managed_audit_api.py -v -k "repo_full_name_to_the_job or verification_token"`
Expected: FAIL - `enqueue` isn't called with `repo_full_name` yet, and the status response doesn't include `verification_token` yet.

- [ ] **Step 7: Wire `repo_full_name` into the enqueue call**

In `github-app/app_server/managed_audit_api.py`, change `start_managed_audit`'s enqueue call from:

```python
    job = _get_queue(get_settings().redis_url).enqueue(
        "scan_worker.jobs.run_managed_audit_api_job",
        job_timeout=900,
        installation_id=installation["installation_id"],
        evidence=body.evidence,
    )
```

to:

```python
    job = _get_queue(get_settings().redis_url).enqueue(
        "scan_worker.jobs.run_managed_audit_api_job",
        job_timeout=900,
        installation_id=installation["installation_id"],
        evidence=body.evidence,
        repo_full_name=body.repo_full_name,
    )
```

- [ ] **Step 8: Surface `verification_token` in the status response**

Change `get_managed_audit_status`'s finished branch from:

```python
    if job.is_finished:
        return {"status": "finished", "result": job.result}
```

to:

```python
    if job.is_finished:
        return {
            "status": "finished",
            "result": job.result,
            "verification_token": job.meta.get("verification_token"),
        }
```

- [ ] **Step 9: Run tests to verify they pass**

Run: `cd github-app && python -m pytest tests/test_managed_audit_api.py -v`
Expected: all PASS.

- [ ] **Step 10: Run the full github-app suite to check for regressions**

Run: `cd github-app && python -m pytest -q`
Expected: all PASS.

- [ ] **Step 11: Commit**

```bash
git add github-app/scan_worker/jobs.py github-app/app_server/managed_audit_api.py github-app/tests/test_jobs.py github-app/tests/test_managed_audit_api.py
git commit -m "feat: sign and persist API-triggered managed audit reports"
```

---

## Self-Review

**Spec coverage:**
- API-triggered audits get signed and persisted the same way PR-triggered ones do → Task 2. ✅
- Doesn't break the CLI's existing `-> str` contract → `job.result` is untouched throughout; the new data rides on `job.meta`, confirmed by re-reading `managed_audit_client.py` before design (it only reads `body["result"]` and ignores unknown fields). ✅
- Fail-open in both places, not just the new one → Task 1 fixes the PR path's existing fragility (a real, previously-unnoticed bug where a DB hiccup during signing replaced the entire audit with a generic failure comment) before Task 2 builds the API path on the same, now-safe pattern. ✅

**Placeholder scan:** No "TBD"/"TODO" in any task; every step shows complete code.

**Type consistency:** `_sign_and_persist_audit_report(settings, installation_id, repo_full_name, report_text) -> str | None` is defined once in Task 1 and consumed identically (positionally) in both `run_managed_audit_pr_job` (Task 1) and `run_managed_audit_api_job` (Task 2). `run_managed_audit_api_job`'s new `repo_full_name: str` parameter matches exactly what `start_managed_audit` now passes via `enqueue(..., repo_full_name=body.repo_full_name)` - and `body.repo_full_name` is already validated non-empty earlier in that same function (`if not body.repo_full_name: raise HTTPException(400, ...)`), so the job never receives an empty string.

**Scope check:** Two tasks, each independently testable and shippable on its own - Task 1 is a pure bug fix with no dependency on Task 2; Task 2 depends on Task 1's helper but not the reverse. `managed_audit_client.py` and the CLI's MCP `aletheore_managed_audit` tool are both deliberately untouched, matching the plan's stated scope boundary.
