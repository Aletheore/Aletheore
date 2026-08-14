import hashlib
from unittest.mock import AsyncMock, MagicMock

import pytest
from httpx import ASGITransport, AsyncClient

from aletheore.air_schema import AIR_JSON_SCHEMA
from aletheore.evidence import EVIDENCE_VERSION
from aletheore.toon_encoding import to_toon
from app_server.db import create_api_token, set_installation_plan, upsert_installation
from app_server.evidence_limits import MAX_EVIDENCE_BYTES
from app_server.main import app
from app_server.audit_signing import content_hash, public_key_hex_from_private, sign_report, verify_report


def _minimal_instance(schema: dict):
    types = schema.get("type")
    kind = types[0] if isinstance(types, list) else types
    if kind == "object":
        properties = schema.get("properties", {})
        return {key: _minimal_instance(properties[key]) for key in schema.get("required", [])}
    return {"array": [], "string": "", "integer": 0, "number": 0, "boolean": False}.get(kind)


def _evidence_toon(total_loc: int = 100) -> str:
    evidence = _minimal_instance(AIR_JSON_SCHEMA)
    evidence["aletheore_version"] = EVIDENCE_VERSION
    evidence["repository"]["languages"] = [{"name": "Python", "file_count": 1, "loc": total_loc}]
    return to_toon(evidence)


@pytest.mark.asyncio
async def test_verify_audit_report_returns_verified_true_for_untampered_report(pool, monkeypatch):
    monkeypatch.setenv("AUDIT_SIGNING_PRIVATE_KEY", "11" * 32)
    await upsert_installation(pool, 601, "octocat")
    report_text = "the audit findings"
    signature = sign_report(report_text, "11" * 32)
    await pool.execute(
        """
        INSERT INTO audit_reports
            (installation_id, repo_full_name, verification_token, report_text, content_hash, signature)
        VALUES (601, 'octocat/hello-world', 'tok-real', $1, $2, $3)
        """,
        report_text,
        content_hash(report_text),
        signature,
    )
    app.state.db_pool = pool
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.get("/v1/audit/tok-real/verify")

    assert response.status_code == 200
    body = response.json()
    assert body["verified"] is True
    assert body["repo_full_name"] == "octocat/hello-world"
    assert body["content_hash"] == content_hash(report_text)
    assert "report_text" not in body
    assert body["algorithm"] == "Ed25519"
    assert body["signature"] == signature
    assert body["public_key"] == public_key_hex_from_private("11" * 32)
    # The point of returning signature + public_key is that a caller who
    # already has the report text (e.g. from the PR comment it was posted
    # alongside) can verify authenticity themselves, without trusting this
    # endpoint's own "verified" boolean.
    assert verify_report(report_text, body["signature"], body["public_key"]) is True


@pytest.mark.asyncio
async def test_verify_audit_report_returns_verified_false_for_tampered_report(pool, monkeypatch):
    monkeypatch.setenv("AUDIT_SIGNING_PRIVATE_KEY", "11" * 32)
    await upsert_installation(pool, 602, "octocat")
    real_signature = sign_report("the original report", "11" * 32)
    await pool.execute(
        """
        INSERT INTO audit_reports
            (installation_id, repo_full_name, verification_token, report_text, content_hash, signature)
        VALUES (602, 'octocat/hello-world', 'tok-tampered', 'a tampered report', $1, $2)
        """,
        content_hash("the original report"),
        real_signature,
    )
    app.state.db_pool = pool
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.get("/v1/audit/tok-tampered/verify")

    assert response.status_code == 200
    assert response.json()["verified"] is False


@pytest.mark.asyncio
async def test_verify_audit_report_404s_for_unknown_token(pool, monkeypatch):
    monkeypatch.setenv("AUDIT_SIGNING_PRIVATE_KEY", "11" * 32)
    app.state.db_pool = pool
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.get("/v1/audit/does-not-exist/verify")

    assert response.status_code == 404


@pytest.mark.asyncio
async def test_managed_audit_requires_bearer_token(pool):
    app.state.db_pool = pool
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.post(
            "/v1/managed-audit", json={"evidence": _evidence_toon(), "repo_full_name": "octocat/widgets"}
        )
    assert response.status_code == 401


@pytest.mark.asyncio
async def test_managed_audit_returns_422_for_oversized_evidence(pool):
    await upsert_installation(pool, 100, "octocat")
    await set_installation_plan(pool, 100, "indie")
    token_hash = hashlib.sha256(b"real-token").hexdigest()
    await create_api_token(pool, 100, token_hash, "laptop", "octocat")
    app.state.db_pool = pool
    transport = ASGITransport(app=app)
    oversized_evidence = "x" * (MAX_EVIDENCE_BYTES + 1)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.post(
            "/v1/managed-audit",
            json={"evidence": oversized_evidence, "repo_full_name": "octocat/widgets"},
            headers={"Authorization": "Bearer real-token"},
        )
    assert response.status_code == 422


@pytest.mark.asyncio
async def test_managed_audit_rejects_free_plan(pool):
    await upsert_installation(pool, 100, "octocat")
    token_hash = hashlib.sha256(b"real-token").hexdigest()
    await create_api_token(pool, 100, token_hash, "laptop", "octocat")
    app.state.db_pool = pool
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.post(
            "/v1/managed-audit",
            json={"evidence": _evidence_toon(), "repo_full_name": "octocat/widgets"},
            headers={"Authorization": "Bearer real-token"},
        )
    assert response.status_code == 402


@pytest.mark.asyncio
async def test_managed_audit_returns_422_for_missing_evidence(pool):
    await upsert_installation(pool, 100, "octocat")
    await set_installation_plan(pool, 100, "indie")
    token_hash = hashlib.sha256(b"real-token").hexdigest()
    await create_api_token(pool, 100, token_hash, "laptop", "octocat")
    app.state.db_pool = pool
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.post(
            "/v1/managed-audit",
            json={"repo_full_name": "octocat/widgets"},
            headers={"Authorization": "Bearer real-token"},
        )
    assert response.status_code == 422


@pytest.mark.asyncio
async def test_managed_audit_requires_repo_full_name(pool):
    await upsert_installation(pool, 100, "octocat")
    await set_installation_plan(pool, 100, "indie")
    token_hash = hashlib.sha256(b"real-token").hexdigest()
    await create_api_token(pool, 100, token_hash, "laptop", "octocat")
    app.state.db_pool = pool
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.post(
            "/v1/managed-audit",
            json={"evidence": _evidence_toon()},
            headers={"Authorization": "Bearer real-token"},
        )
    assert response.status_code == 400


@pytest.mark.asyncio
async def test_managed_audit_rejects_malformed_air_before_reserving_quota(monkeypatch):
    async def fake_authenticate(_request):
        return {"installation_id": 100, "plan": "indie"}, "token-hash"

    reserve_repo_slot = AsyncMock()
    reserve_audit = AsyncMock()
    fake_queue = MagicMock()
    monkeypatch.setattr("app_server.managed_audit_api._authenticate_token", fake_authenticate)
    monkeypatch.setattr(
        "app_server.managed_audit_api.check_and_reserve_monthly_repo_scan_slot", reserve_repo_slot
    )
    monkeypatch.setattr("app_server.managed_audit_api.check_and_reserve_managed_audit", reserve_audit)
    monkeypatch.setattr("app_server.managed_audit_api._get_queue", lambda redis_url: fake_queue)

    app.state.db_pool = object()
    transport = ASGITransport(app=app)
    malformed_evidence = to_toon({"repository": {"languages": []}})
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.post(
            "/v1/managed-audit",
            json={"evidence": malformed_evidence, "repo_full_name": "octocat/widgets"},
            headers={"Authorization": "Bearer real-token"},
        )

    assert response.status_code == 400
    assert "job_id" not in response.json()
    reserve_repo_slot.assert_not_awaited()
    reserve_audit.assert_not_awaited()
    fake_queue.enqueue.assert_not_called()


@pytest.mark.asyncio
async def test_managed_audit_enqueues_job_for_paid_token(pool, monkeypatch):
    await upsert_installation(pool, 100, "octocat")
    await set_installation_plan(pool, 100, "indie")
    token_hash = hashlib.sha256(b"real-token").hexdigest()
    await create_api_token(pool, 100, token_hash, "laptop", "octocat")
    fake_job = MagicMock(id="job-123")
    fake_queue = MagicMock()
    fake_queue.enqueue.return_value = fake_job
    monkeypatch.setattr("app_server.managed_audit_api._get_queue", lambda redis_url: fake_queue)

    app.state.db_pool = pool
    transport = ASGITransport(app=app)
    evidence_toon = _evidence_toon()
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.post(
            "/v1/managed-audit",
            json={"evidence": evidence_toon, "repo_full_name": "octocat/widgets"},
            headers={"Authorization": "Bearer real-token"},
        )

    assert response.status_code == 202
    assert response.json()["job_id"] == "job-123"
    args, kwargs = fake_queue.enqueue.call_args
    assert args[0] == "scan_worker.jobs.run_managed_audit_api_job"
    assert kwargs["evidence"] == evidence_toon
    assert kwargs["installation_id"] == 100
    assert kwargs["job_timeout"] >= 600


@pytest.mark.asyncio
async def test_managed_audit_blocks_second_request_within_cooldown(pool, monkeypatch):
    await upsert_installation(pool, 100, "octocat")
    await set_installation_plan(pool, 100, "indie")
    token_hash = hashlib.sha256(b"real-token").hexdigest()
    await create_api_token(pool, 100, token_hash, "laptop", "octocat")
    fake_queue = MagicMock()
    fake_queue.enqueue.return_value = MagicMock(id="job-123")
    monkeypatch.setattr("app_server.managed_audit_api._get_queue", lambda redis_url: fake_queue)

    app.state.db_pool = pool
    transport = ASGITransport(app=app)
    body = {"evidence": _evidence_toon(), "repo_full_name": "octocat/widgets"}
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        first = await client.post(
            "/v1/managed-audit", json=body, headers={"Authorization": "Bearer real-token"}
        )
        second = await client.post(
            "/v1/managed-audit", json=body, headers={"Authorization": "Bearer real-token"}
        )

    assert first.status_code == 202
    assert second.status_code == 429


@pytest.mark.asyncio
async def test_managed_audit_rate_limit_is_independent_per_repo(pool, monkeypatch):
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
        first = await client.post(
            "/v1/managed-audit",
            json={"evidence": _evidence_toon(), "repo_full_name": "octocat/widgets"},
            headers={"Authorization": "Bearer real-token"},
        )
        second = await client.post(
            "/v1/managed-audit",
            json={"evidence": _evidence_toon(), "repo_full_name": "octocat/gizmos"},
            headers={"Authorization": "Bearer real-token"},
        )

    assert first.status_code == 202
    assert second.status_code == 202


@pytest.mark.asyncio
async def test_managed_audit_blocks_11th_new_repo_this_month(pool, monkeypatch):
    await upsert_installation(pool, 100, "octocat")
    await set_installation_plan(pool, 100, "indie")
    token_hash = hashlib.sha256(b"real-token").hexdigest()
    await create_api_token(pool, 100, token_hash, "laptop", "octocat")
    for i in range(10):
        await pool.execute(
            """
            INSERT INTO monthly_scanned_repos (installation_id, repo_full_name, month)
            VALUES (100, $1, date_trunc('month', now())::date)
            """,
            f"octocat/repo-{i}",
        )
    fake_queue = MagicMock()
    fake_queue.enqueue.return_value = MagicMock(id="job-123")
    monkeypatch.setattr("app_server.managed_audit_api._get_queue", lambda redis_url: fake_queue)

    app.state.db_pool = pool
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.post(
            "/v1/managed-audit",
            json={"evidence": _evidence_toon(), "repo_full_name": "octocat/the-11th-repo"},
            headers={"Authorization": "Bearer real-token"},
        )

    assert response.status_code == 429
    fake_queue.enqueue.assert_not_called()


@pytest.mark.asyncio
async def test_managed_audit_new_repo_limit_does_not_block_repeat_audit(pool, monkeypatch):
    # A repo that's already one of this installation's counted repos this
    # month must not count against the cap again on a repeat run - only
    # genuinely new repos do.
    await upsert_installation(pool, 100, "octocat")
    await set_installation_plan(pool, 100, "indie")
    token_hash = hashlib.sha256(b"real-token").hexdigest()
    await create_api_token(pool, 100, token_hash, "laptop", "octocat")
    # Already at the cap with 10 *other* repos - if the already-counted
    # bypass didn't work, a repeat run for octocat/widgets below would be
    # blocked too.
    for i in range(10):
        await pool.execute(
            """
            INSERT INTO monthly_scanned_repos (installation_id, repo_full_name, month)
            VALUES (100, $1, date_trunc('month', now())::date)
            """,
            f"octocat/repo-{i}",
        )
    # octocat/widgets already ran an audit this month, well outside its own cooldown.
    await pool.execute(
        """
        INSERT INTO monthly_scanned_repos (installation_id, repo_full_name, month)
        VALUES (100, 'octocat/widgets', date_trunc('month', now())::date)
        """
    )
    await pool.execute(
        """
        INSERT INTO managed_audit_rate_limits (installation_id, repo_full_name, last_run_at)
        VALUES (100, 'octocat/widgets', now() - interval '1 day')
        """
    )
    fake_queue = MagicMock()
    fake_queue.enqueue.return_value = MagicMock(id="job-123")
    monkeypatch.setattr("app_server.managed_audit_api._get_queue", lambda redis_url: fake_queue)

    app.state.db_pool = pool
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.post(
            "/v1/managed-audit",
            json={"evidence": _evidence_toon(), "repo_full_name": "octocat/widgets"},
            headers={"Authorization": "Bearer real-token"},
        )

    assert response.status_code == 202


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


@pytest.mark.asyncio
async def test_get_job_status_requires_bearer_token(pool):
    app.state.db_pool = pool
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.get("/v1/managed-audit/job-123")
    assert response.status_code == 401


@pytest.mark.asyncio
async def test_get_job_status_returns_result_when_finished(pool, monkeypatch):
    await upsert_installation(pool, 100, "octocat")
    await set_installation_plan(pool, 100, "indie")
    token_hash = hashlib.sha256(b"real-token").hexdigest()
    await create_api_token(pool, 100, token_hash, "laptop", "octocat")

    fake_job = MagicMock(
        is_finished=True,
        is_failed=False,
        result="# Report",
        kwargs={"installation_id": 100},
        meta={},
    )
    monkeypatch.setattr("app_server.managed_audit_api._fetch_job", lambda job_id, redis_url: fake_job)

    app.state.db_pool = pool
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.get(
            "/v1/managed-audit/job-123", headers={"Authorization": "Bearer real-token"}
        )

    assert response.status_code == 200
    assert response.json() == {
        "status": "finished",
        "result": "# Report",
        "verification_token": None,
    }


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
            "/v1/managed-audit/job-123",
            headers={"Authorization": "Bearer real-token"},
        )

    assert response.status_code == 200
    assert response.json() == {
        "status": "finished",
        "result": "# Report",
        "verification_token": "abc123",
    }


@pytest.mark.asyncio
async def test_get_job_status_rejects_job_belonging_to_another_installation(pool, monkeypatch):
    await upsert_installation(pool, 100, "octocat")
    await set_installation_plan(pool, 100, "indie")
    token_hash = hashlib.sha256(b"real-token").hexdigest()
    await create_api_token(pool, 100, token_hash, "laptop", "octocat")

    # This job was enqueued for a different installation (999) - a valid
    # token for installation 100 must not be able to read its result.
    fake_job = MagicMock(is_finished=True, is_failed=False, result="# Report", kwargs={"installation_id": 999})
    monkeypatch.setattr("app_server.managed_audit_api._fetch_job", lambda job_id, redis_url: fake_job)

    app.state.db_pool = pool
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.get(
            "/v1/managed-audit/other-installations-job", headers={"Authorization": "Bearer real-token"}
        )

    assert response.status_code == 404


@pytest.mark.asyncio
async def test_get_job_status_returns_404_for_unknown_job(pool, monkeypatch):
    from rq.exceptions import NoSuchJobError

    await upsert_installation(pool, 100, "octocat")
    await set_installation_plan(pool, 100, "indie")
    token_hash = hashlib.sha256(b"real-token").hexdigest()
    await create_api_token(pool, 100, token_hash, "laptop", "octocat")

    def _raise(job_id, redis_url):
        raise NoSuchJobError(job_id)

    monkeypatch.setattr("app_server.managed_audit_api._fetch_job", _raise)

    app.state.db_pool = pool
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.get(
            "/v1/managed-audit/no-such-job", headers={"Authorization": "Bearer real-token"}
        )

    assert response.status_code == 404


@pytest.mark.asyncio
async def test_whoami_requires_bearer_token(pool):
    app.state.db_pool = pool
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.get("/v1/whoami")
    assert response.status_code == 401


@pytest.mark.asyncio
async def test_whoami_rejects_unknown_token(pool):
    app.state.db_pool = pool
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.get(
            "/v1/whoami", headers={"Authorization": "Bearer no-such-token"}
        )
    assert response.status_code == 401


@pytest.mark.asyncio
async def test_whoami_returns_account_login_and_plan_for_valid_token(pool):
    await upsert_installation(pool, 100, "acme")
    await set_installation_plan(pool, 100, "indie")
    token_hash = hashlib.sha256(b"real-token").hexdigest()
    await create_api_token(pool, 100, token_hash, "laptop", "acme")

    app.state.db_pool = pool
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.get(
            "/v1/whoami", headers={"Authorization": "Bearer real-token"}
        )

    assert response.status_code == 200
    assert response.json() == {"account_login": "acme", "plan": "indie"}


@pytest.mark.asyncio
async def test_verify_uses_the_key_that_signed_the_report_after_a_rotation(pool, monkeypatch):
    # The regression: the public key was derived from whatever
    # AUDIT_SIGNING_PRIVATE_KEY was set to at request time, so rotating the
    # signing key reported verified=false for every certificate ever issued.
    old_key = "11" * 32
    new_key = "22" * 32
    monkeypatch.setenv("AUDIT_SIGNING_PRIVATE_KEY", old_key)
    await upsert_installation(pool, 611, "octocat")
    report_text = "signed before the rotation"
    await pool.execute(
        """
        INSERT INTO audit_reports
            (installation_id, repo_full_name, verification_token, report_text,
             content_hash, signature, signing_public_key)
        VALUES (611, 'octocat/hello-world', 'tok-rotated', $1, $2, $3, $4)
        """,
        report_text,
        content_hash(report_text),
        sign_report(report_text, old_key),
        public_key_hex_from_private(old_key),
    )

    # The key rotates; the already-issued certificate must still verify.
    monkeypatch.setenv("AUDIT_SIGNING_PRIVATE_KEY", new_key)
    from app_server.config import get_settings

    get_settings.cache_clear()

    app.state.db_pool = pool
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.get("/v1/audit/tok-rotated/verify")

    body = response.json()
    assert body["verified"] is True
    assert body["public_key"] == public_key_hex_from_private(old_key)
    assert body["is_current_key"] is False


@pytest.mark.asyncio
async def test_verify_falls_back_to_the_current_key_for_pre_migration_rows(pool, monkeypatch):
    # Rows written before migration 044 have no recorded key and were signed
    # by whatever key is current - the pre-migration behaviour, preserved for
    # those rows alone.
    key = "33" * 32
    monkeypatch.setenv("AUDIT_SIGNING_PRIVATE_KEY", key)
    await upsert_installation(pool, 612, "octocat")
    report_text = "signed before migration 044"
    await pool.execute(
        """
        INSERT INTO audit_reports
            (installation_id, repo_full_name, verification_token, report_text, content_hash, signature)
        VALUES (612, 'octocat/hello-world', 'tok-legacy', $1, $2, $3)
        """,
        report_text,
        content_hash(report_text),
        sign_report(report_text, key),
    )

    app.state.db_pool = pool
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.get("/v1/audit/tok-legacy/verify")

    body = response.json()
    assert body["verified"] is True
    assert body["is_current_key"] is True


@pytest.mark.asyncio
async def test_signing_key_endpoint_serves_the_current_public_key(monkeypatch):
    key = "44" * 32
    monkeypatch.setenv("AUDIT_SIGNING_PRIVATE_KEY", key)
    from app_server.config import get_settings

    get_settings.cache_clear()

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.get("/v1/audit/signing-key")

    assert response.status_code == 200
    assert response.json() == {
        "algorithm": "Ed25519",
        "public_key": public_key_hex_from_private(key),
    }
