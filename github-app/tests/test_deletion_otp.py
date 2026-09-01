import hashlib
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from test_admin import _logged_in_client


@pytest.mark.asyncio
async def test_request_otp_requires_login(pool, monkeypatch):
    client = await _logged_in_client(pool, monkeypatch)
    async with client:
        client.cookies.clear()
        response = await client.post("/admin/octocat/hello-world/delete-all-data/request-otp")

    assert response.status_code == 401


@pytest.mark.asyncio
async def test_request_otp_rejects_non_administrator(pool, monkeypatch):
    # 404, not 403 - a real installation the caller doesn't administer must
    # be indistinguishable from one that doesn't exist at all
    # (docs/audits/Claude_Audit.md finding 34).
    from app_server.db import upsert_installation

    client = await _logged_in_client(pool, monkeypatch, installation_id=100)
    await upsert_installation(pool, 101, "globex")
    async with client:
        response = await client.post("/admin/globex/web/delete-all-data/request-otp")

    assert response.status_code == 404


@pytest.mark.asyncio
async def test_request_otp_works_on_the_free_plan(pool, monkeypatch):
    client = await _logged_in_client(pool, monkeypatch, plan="free")
    await pool.execute(
        "INSERT INTO github_user_emails (github_login, email) VALUES ('octocat', 'octocat@example.com') "
        "ON CONFLICT (github_login) DO UPDATE SET email = EXCLUDED.email"
    )
    monkeypatch.setattr("app_server.admin.is_rate_limited", lambda *a, **k: False)
    async with client:
        response = await client.post("/admin/octocat/hello-world/delete-all-data/request-otp")

    assert response.status_code == 200, response.text


@pytest.mark.asyncio
async def test_request_otp_requires_a_verified_email_on_file(pool, monkeypatch):
    # _logged_in_client seeds a session for octocat but no email row -
    # exactly the state of a real user who's never had their email captured.
    client = await _logged_in_client(pool, monkeypatch)
    async with client:
        response = await client.post("/admin/octocat/hello-world/delete-all-data/request-otp")

    assert response.status_code == 400
    assert "email" in response.json()["detail"]


@pytest.mark.asyncio
async def test_request_otp_masks_the_email_in_its_response(pool, monkeypatch):
    client = await _logged_in_client(pool, monkeypatch)
    await pool.execute(
        "INSERT INTO github_user_emails (github_login, email) VALUES ('octocat', 'octocat@example.com') "
        "ON CONFLICT (github_login) DO UPDATE SET email = EXCLUDED.email"
    )
    monkeypatch.setattr("app_server.admin.is_rate_limited", lambda *a, **k: False)
    async with client:
        response = await client.post("/admin/octocat/hello-world/delete-all-data/request-otp")

    assert response.status_code == 200, response.text
    sent_to = response.json()["sent_to"]
    assert "octocat@example.com" not in sent_to
    assert sent_to.endswith("@example.com")


@pytest.mark.asyncio
async def test_request_otp_stores_a_hash_not_the_plaintext_code(pool, monkeypatch):
    monkeypatch.setattr("app_server.admin.secrets.randbelow", lambda _n: 424242)
    client = await _logged_in_client(pool, monkeypatch)
    await pool.execute(
        "INSERT INTO github_user_emails (github_login, email) VALUES ('octocat', 'octocat@example.com') "
        "ON CONFLICT (github_login) DO UPDATE SET email = EXCLUDED.email"
    )
    monkeypatch.setattr("app_server.admin.is_rate_limited", lambda *a, **k: False)
    async with client:
        response = await client.post("/admin/octocat/hello-world/delete-all-data/request-otp")
    assert response.status_code == 200, response.text

    row = await pool.fetchrow(
        "SELECT code_hash, requested_by FROM deletion_otp_codes WHERE installation_id = 100 "
        "ORDER BY created_at DESC LIMIT 1"
    )
    assert row["requested_by"] == "octocat"
    assert row["code_hash"] == hashlib.sha256(b"424242").hexdigest()
    assert row["code_hash"] != "424242"


@pytest.mark.asyncio
async def test_request_otp_is_rate_limited(pool, monkeypatch):
    # Same pattern as test_ingest_abuse_controls.py: mock is_rate_limited
    # itself rather than driving a real Redis six times - it's the
    # response to a True verdict this test is checking, not is_rate_limited's
    # own counting logic (that's rate_limit.py's own test coverage).
    client = await _logged_in_client(pool, monkeypatch)
    await pool.execute(
        "INSERT INTO github_user_emails (github_login, email) VALUES ('octocat', 'octocat@example.com') "
        "ON CONFLICT (github_login) DO UPDATE SET email = EXCLUDED.email"
    )
    monkeypatch.setattr("app_server.admin.is_rate_limited", lambda *a, **k: True)
    async with client:
        response = await client.post("/admin/octocat/hello-world/delete-all-data/request-otp")

    assert response.status_code == 429


@pytest.mark.asyncio
async def test_full_otp_flow_end_to_end(pool, monkeypatch):
    """The real path a browser drives: request a code, then use it."""
    monkeypatch.setattr("app_server.admin.secrets.randbelow", lambda _n: 424242)
    client = await _logged_in_client(pool, monkeypatch)
    await pool.execute(
        "INSERT INTO github_user_emails (github_login, email) VALUES ('octocat', 'octocat@example.com') "
        "ON CONFLICT (github_login) DO UPDATE SET email = EXCLUDED.email"
    )
    monkeypatch.setattr("app_server.admin.is_rate_limited", lambda *a, **k: False)
    monkeypatch.setattr("rq.Queue.enqueue", MagicMock())
    async with client:
        request_response = await client.post("/admin/octocat/hello-world/delete-all-data/request-otp")
        assert request_response.status_code == 200, request_response.text

        delete_response = await client.post(
            "/admin/octocat/hello-world/delete-all-data",
            json={"confirm": "octocat", "otp_code": "424242"},
        )

    assert delete_response.status_code == 200, delete_response.text


@pytest.mark.asyncio
async def test_otp_rate_limit_checks_are_offloaded_to_thread(pool, monkeypatch):
    # Real regression this guards: is_rate_limited uses the synchronous
    # redis-py client and blocks on pipe.execute() - called directly inside
    # an async def handler, each check stalls the whole event loop (every
    # other concurrent request on this worker) for its full duration. See
    # embeddings_api.py's identical test for the pattern this was copied
    # from (#328's own original fix). Covers both call sites in admin.py:
    # the OTP request itself and the delete-all-data attempt limiter.
    import app_server.admin as admin_module

    monkeypatch.setattr("app_server.admin.secrets.randbelow", lambda _n: 424242)
    client = await _logged_in_client(pool, monkeypatch)
    await pool.execute(
        "INSERT INTO github_user_emails (github_login, email) VALUES ('octocat', 'octocat@example.com') "
        "ON CONFLICT (github_login) DO UPDATE SET email = EXCLUDED.email"
    )
    monkeypatch.setattr("rq.Queue.enqueue", MagicMock())

    offloaded_funcs = []

    async def _dispatch(func, *args, **kwargs):
        offloaded_funcs.append(func)
        return func(*args, **kwargs)

    async with client:
        with patch.object(admin_module.asyncio, "to_thread", AsyncMock(side_effect=_dispatch)):
            request_response = await client.post("/admin/octocat/hello-world/delete-all-data/request-otp")
            assert request_response.status_code == 200, request_response.text

            delete_response = await client.post(
                "/admin/octocat/hello-world/delete-all-data",
                json={"confirm": "octocat", "otp_code": "424242"},
            )
            assert delete_response.status_code == 200, delete_response.text

    assert offloaded_funcs.count(admin_module.is_rate_limited) == 2
