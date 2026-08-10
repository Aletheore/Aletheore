from datetime import datetime, timedelta, timezone

import pytest

from app_server.auth import encrypt_access_token
from app_server.db import (
    add_installation_member,
    create_session,
    get_installation,
    get_session,
    insert_repo_history,
    purge_installation_data,
    record_installation_access,
    set_installation_plan,
    upsert_installation,
)
from app_server.webhooks.installation import handle_installation_event
from test_admin import _logged_in_client


async def _seed_installation(pool, installation_id, account_login, repo_full_name):
    await upsert_installation(pool, installation_id, account_login)
    await insert_repo_history(
        pool,
        installation_id,
        repo_full_name,
        datetime.now(timezone.utc),
        {"scanned_at": "x"},
    )


async def _get_otp_code(client, delete_path, monkeypatch):
    """Drives the real request-otp endpoint and returns the real code it
    generated. The code is deliberately never returned by the API (it only
    goes out by email) - randbelow is pinned so the test can know it
    without needing to intercept an actual email send. is_rate_limited is
    mocked to skip a real (unreachable in this test env) Redis round-trip -
    rate limiting itself has its own dedicated test.
    """
    monkeypatch.setattr("app_server.admin.secrets.randbelow", lambda _n: 424242)
    monkeypatch.setattr("app_server.admin.is_rate_limited", lambda *a, **k: False)
    response = await client.post(f"{delete_path}/request-otp")
    assert response.status_code == 200, response.text
    return "424242"


async def _seed_user(pool, session_id, login, email, user_id=1):
    await create_session(
        pool,
        session_id,
        user_id,
        login,
        encrypt_access_token("gho_faketoken", "test-session-secret"),
        datetime.now(timezone.utc) + timedelta(hours=1),
    )
    await pool.execute(
        "INSERT INTO github_user_emails (github_login, email) VALUES ($1, $2) "
        "ON CONFLICT (github_login) DO UPDATE SET email = EXCLUDED.email",
        login,
        email,
    )


@pytest.mark.asyncio
async def test_purge_removes_installation_and_cascading_evidence(pool):
    await _seed_installation(pool, 900, "acme", "acme/api")

    result = await purge_installation_data(pool, 900, "octocat")

    assert result["repos_deleted"] == 1
    assert await get_installation(pool, 900) is None
    remaining = await pool.fetchval(
        "SELECT count(*) FROM repo_history WHERE installation_id = 900"
    )
    assert remaining == 0


@pytest.mark.asyncio
async def test_purge_writes_audit_row_that_survives_its_own_deletion(pool):
    await _seed_installation(pool, 901, "acme", "acme/api")

    await purge_installation_data(pool, 901, "octocat")

    row = await pool.fetchrow(
        "SELECT * FROM data_deletion_log WHERE installation_id = 901"
    )
    assert row is not None, "audit row cascaded away with the installation it documents"
    assert row["account_login"] == "acme"
    assert row["actor_login"] == "octocat"
    assert row["repos_deleted"] == 1


@pytest.mark.asyncio
async def test_purge_clears_email_and_session_of_orphaned_member(pool):
    await _seed_installation(pool, 902, "acme", "acme/api")
    await record_installation_access(pool, 902, "solo-dev")
    await _seed_user(pool, "sess-solo", "solo-dev", "solo@example.com")

    result = await purge_installation_data(pool, 902, "solo-dev")

    assert result["users_purged"] == 1
    assert await get_session(pool, "sess-solo") is None
    email = await pool.fetchval(
        "SELECT email FROM github_user_emails WHERE github_login = 'solo-dev'"
    )
    assert email is None, "PII survived a full deletion"


@pytest.mark.asyncio
async def test_purge_clears_pii_of_free_plan_member_with_no_seat(pool):
    # The bug this regression-guards: installation_members is only ever
    # populated for paid-plan seat holders (_require_seat_if_paid skips
    # free plans entirely), so a purge that read membership from that table
    # alone would silently leave a free-plan user's email and session
    # behind forever. installation_access_log is plan-independent and
    # deliberately has zero installation_members involvement here.
    await _seed_installation(pool, 907, "acme", "acme/api")
    await set_installation_plan(pool, 907, "free")
    await record_installation_access(pool, 907, "free-plan-dev")
    await _seed_user(pool, "sess-free", "free-plan-dev", "free-dev@example.com")

    members = await pool.fetchval(
        "SELECT count(*) FROM installation_members WHERE installation_id = 907"
    )
    assert members == 0, "test setup should not touch installation_members"

    result = await purge_installation_data(pool, 907, "free-plan-dev")

    assert result["users_purged"] == 1
    assert await get_session(pool, "sess-free") is None
    email = await pool.fetchval(
        "SELECT email FROM github_user_emails WHERE github_login = 'free-plan-dev'"
    )
    assert email is None, "free-plan PII survived a full deletion"


@pytest.mark.asyncio
async def test_purge_keeps_member_who_belongs_to_another_installation(pool):
    await _seed_installation(pool, 903, "acme", "acme/api")
    await _seed_installation(pool, 904, "globex", "globex/web")
    await record_installation_access(pool, 903, "two-orgs")
    await record_installation_access(pool, 904, "two-orgs")
    await _seed_user(pool, "sess-two", "two-orgs", "two@example.com")

    result = await purge_installation_data(pool, 903, "two-orgs")

    assert result["users_purged"] == 0
    assert await get_session(pool, "sess-two") is not None, "logged out of an unrelated org"
    email = await pool.fetchval(
        "SELECT email FROM github_user_emails WHERE github_login = 'two-orgs'"
    )
    assert email == "two@example.com"


@pytest.mark.asyncio
async def test_record_installation_access_upsert_is_idempotent(pool):
    await _seed_installation(pool, 908, "acme", "acme/api")

    await record_installation_access(pool, 908, "solo-dev")
    first = await pool.fetchrow(
        "SELECT first_seen_at, last_seen_at FROM installation_access_log "
        "WHERE installation_id = 908 AND github_login = 'solo-dev'"
    )
    await record_installation_access(pool, 908, "solo-dev")
    second = await pool.fetchrow(
        "SELECT first_seen_at, last_seen_at FROM installation_access_log "
        "WHERE installation_id = 908 AND github_login = 'solo-dev'"
    )

    count = await pool.fetchval(
        "SELECT count(*) FROM installation_access_log WHERE installation_id = 908"
    )
    assert count == 1, "a second visit should update the row, not duplicate it"
    assert second["first_seen_at"] == first["first_seen_at"]
    assert second["last_seen_at"] >= first["last_seen_at"]


@pytest.mark.asyncio
async def test_purge_of_unknown_installation_is_a_no_op(pool):
    assert await purge_installation_data(pool, 999_999, "octocat") is None
    logged = await pool.fetchval(
        "SELECT count(*) FROM data_deletion_log WHERE installation_id = 999999"
    )
    assert logged == 0


@pytest.mark.asyncio
async def test_uninstall_webhook_purges_user_rows_too(pool):
    await _seed_installation(pool, 905, "acme", "acme/api")
    await record_installation_access(pool, 905, "solo-dev")
    await _seed_user(pool, "sess-uninstall", "solo-dev", "solo@example.com")

    payload = {
        "action": "deleted",
        "installation": {"id": 905, "account": {"login": "acme"}},
        "sender": {"login": "solo-dev"},
    }
    await handle_installation_event("installation", payload, pool, "redis://unused")

    assert await get_installation(pool, 905) is None
    assert await get_session(pool, "sess-uninstall") is None
    row = await pool.fetchrow(
        "SELECT actor_login FROM data_deletion_log WHERE installation_id = 905"
    )
    assert row["actor_login"] == "solo-dev"


@pytest.mark.asyncio
async def test_delete_all_data_route_purges_on_correct_confirmation(pool, monkeypatch):
    client = await _logged_in_client(pool, monkeypatch)
    await pool.execute(
        "INSERT INTO github_user_emails (github_login, email) VALUES ('octocat', 'octocat@example.com') "
        "ON CONFLICT (github_login) DO UPDATE SET email = EXCLUDED.email"
    )
    async with client:
        otp_code = await _get_otp_code(client, "/admin/octocat/hello-world/delete-all-data", monkeypatch)
        response = await client.post(
            "/admin/octocat/hello-world/delete-all-data",
            json={"confirm": "octocat", "otp_code": otp_code},
        )

    assert response.status_code == 200, response.text
    assert response.json()["ok"] is True
    assert await get_installation(pool, 100) is None


@pytest.mark.asyncio
async def test_delete_all_data_route_rejects_wrong_confirmation(pool, monkeypatch):
    # A wrong confirm phrase is rejected before the OTP is ever checked -
    # any syntactically valid code works here, since it's never consumed.
    client = await _logged_in_client(pool, monkeypatch)
    async with client:
        response = await client.post(
            "/admin/octocat/hello-world/delete-all-data",
            json={"confirm": "hello-world", "otp_code": "000000"},
        )

    assert response.status_code == 400
    assert await get_installation(pool, 100) is not None, "deleted without confirmation"


@pytest.mark.asyncio
async def test_delete_all_data_route_rejects_missing_otp(pool, monkeypatch):
    client = await _logged_in_client(pool, monkeypatch)
    await pool.execute(
        "INSERT INTO github_user_emails (github_login, email) VALUES ('octocat', 'octocat@example.com') "
        "ON CONFLICT (github_login) DO UPDATE SET email = EXCLUDED.email"
    )
    async with client:
        # Confirm phrase is correct, but no code was ever requested - the
        # typed name alone must never be enough to delete.
        response = await client.post(
            "/admin/octocat/hello-world/delete-all-data",
            json={"confirm": "octocat", "otp_code": "000000"},
        )

    assert response.status_code == 400
    assert "code" in response.json()["detail"]
    assert await get_installation(pool, 100) is not None, "deleted without a valid code"


@pytest.mark.asyncio
async def test_consume_deletion_otp_code_is_single_use(pool):
    from app_server.db import consume_deletion_otp_code, create_deletion_otp_code

    await _seed_installation(pool, 955, "acme-otp", "acme-otp/api")
    expires_at = datetime.now(timezone.utc) + timedelta(minutes=10)
    await create_deletion_otp_code(pool, 955, "octocat", "hash-of-424242", expires_at)

    assert await consume_deletion_otp_code(pool, 955, "hash-of-424242") is True
    assert await consume_deletion_otp_code(pool, 955, "hash-of-424242") is False, \
        "the same code was accepted twice"


@pytest.mark.asyncio
async def test_consume_deletion_otp_code_rejects_an_expired_code(pool):
    from app_server.db import consume_deletion_otp_code, create_deletion_otp_code

    await _seed_installation(pool, 956, "acme-expired", "acme-expired/api")
    expired_at = datetime.now(timezone.utc) - timedelta(minutes=1)
    await create_deletion_otp_code(pool, 956, "octocat", "hash-of-424242", expired_at)

    assert await consume_deletion_otp_code(pool, 956, "hash-of-424242") is False


@pytest.mark.asyncio
async def test_delete_all_data_route_works_on_the_free_plan(pool, monkeypatch):
    # A free-plan customer must still be able to erase their data - gating
    # this route behind a paid plan would be indefensible. This also
    # regression-guards the original bug: a free-plan requester is never a
    # row in installation_members (_require_seat_if_paid skips free plans),
    # so if the route's purge fell back to reading membership from that
    # table, this would report success while leaving the caller's own
    # email and session behind.
    client = await _logged_in_client(pool, monkeypatch, plan="free")
    await pool.execute(
        "INSERT INTO github_user_emails (github_login, email) VALUES ('octocat', 'octocat@example.com') "
        "ON CONFLICT (github_login) DO UPDATE SET email = EXCLUDED.email"
    )
    async with client:
        otp_code = await _get_otp_code(client, "/admin/octocat/hello-world/delete-all-data", monkeypatch)
        response = await client.post(
            "/admin/octocat/hello-world/delete-all-data",
            json={"confirm": "octocat", "otp_code": otp_code},
        )

    assert response.status_code == 200, response.text
    assert await get_installation(pool, 100) is None
    assert await get_session(pool, "sess-1") is None, "free-plan requester's own session survived"
    email = await pool.fetchval(
        "SELECT email FROM github_user_emails WHERE github_login = 'octocat'"
    )
    assert email is None, "free-plan requester's own email survived"


@pytest.mark.asyncio
async def test_delete_all_data_route_rejects_non_administrator(pool, monkeypatch):
    # 403 fires inside _require_authorized_installation, before the OTP is
    # ever looked at - "000000" just needs to be syntactically valid so
    # Pydantic doesn't 422 the request before that check even runs.
    client = await _logged_in_client(pool, monkeypatch, installation_id=100)
    # A second installation this session does not administer - the mocked
    # GitHub /user/installations response only ever returns id 100.
    await _seed_installation(pool, 101, "globex", "globex/web")
    async with client:
        response = await client.post(
            "/admin/globex/web/delete-all-data",
            json={"confirm": "globex", "otp_code": "000000"},
        )

    assert response.status_code == 403
    assert await get_installation(pool, 101) is not None


@pytest.mark.asyncio
async def test_delete_all_data_route_requires_login(pool, monkeypatch):
    client = await _logged_in_client(pool, monkeypatch)
    async with client:
        client.cookies.clear()
        response = await client.post(
            "/admin/octocat/hello-world/delete-all-data",
            json={"confirm": "octocat", "otp_code": "000000"},
        )

    assert response.status_code == 401
    assert await get_installation(pool, 100) is not None


@pytest.mark.asyncio
async def test_delete_all_data_route_404s_if_installation_vanishes_mid_request(pool, monkeypatch):
    # Concurrent uninstall: authorization and the OTP both passed, but the
    # row is gone by the time the purge runs. Must not report success for
    # a delete that no-oped.
    client = await _logged_in_client(pool, monkeypatch)
    await pool.execute(
        "INSERT INTO github_user_emails (github_login, email) VALUES ('octocat', 'octocat@example.com') "
        "ON CONFLICT (github_login) DO UPDATE SET email = EXCLUDED.email"
    )

    async def _already_gone(*_args, **_kwargs):
        return None

    async with client:
        otp_code = await _get_otp_code(client, "/admin/octocat/hello-world/delete-all-data", monkeypatch)
        monkeypatch.setattr("app_server.admin.purge_installation_data", _already_gone)
        response = await client.post(
            "/admin/octocat/hello-world/delete-all-data",
            json={"confirm": "octocat", "otp_code": otp_code},
        )

    assert response.status_code == 404


@pytest.mark.asyncio
async def test_deletion_preview_names_every_repo_in_the_installation(pool, monkeypatch):
    client = await _logged_in_client(pool, monkeypatch)
    await insert_repo_history(
        pool, 100, "octocat/second-repo", datetime.now(timezone.utc), {"scanned_at": "x"}
    )
    async with client:
        response = await client.get("/admin/octocat/hello-world/deletion-preview")

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["account_login"] == "octocat"
    # The blast radius is installation-wide, so a repo the user isn't
    # looking at must still be named in the confirmation.
    assert "octocat/second-repo" in body["repos"]


@pytest.mark.asyncio
async def test_purge_leaves_no_installation_scoped_rows_behind(pool, monkeypatch):
    await _seed_installation(pool, 906, "acme", "acme/api")
    await set_installation_plan(pool, 906, "air")
    await add_installation_member(pool, 906, "solo-dev", "solo-dev")
    await record_installation_access(pool, 906, "solo-dev")

    await purge_installation_data(pool, 906, "solo-dev")

    for table in (
        "repo_history",
        "installation_members",
        "installation_access_log",
        "monthly_scanned_repos",
    ):
        remaining = await pool.fetchval(
            f"SELECT count(*) FROM {table} WHERE installation_id = 906"  # noqa: S608
        )
        assert remaining == 0, f"{table} survived the cascade"
