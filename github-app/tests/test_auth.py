from datetime import datetime, timedelta, timezone

import httpx
import pytest
from httpx import ASGITransport, AsyncClient

from app_server.auth import (
    encrypt_access_token,
    get_current_session,
    refresh_github_access_token,
    sign_session_id,
    unsign_session_id,
    _derive_key,
    _fernet_key,
    _is_safe_next_path,
    _signing_secret,
)
from app_server.db import create_session, get_installation, get_session
from app_server.main import app


@pytest.mark.asyncio
async def test_signin_page_is_not_cacheable(pool):
    # A cached or bfcache-restored copy of an auth-flow page could let
    # someone hit Back after Sign out and see stale page state without a
    # real request ever reaching the server to re-check the session.
    app.state.db_pool = pool
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.get("/")
    assert response.headers["cache-control"] == "no-store"


@pytest.mark.asyncio
async def test_get_session_returns_none_for_expired_session(pool):
    monkeypatch_secret = "test-session-secret"
    encrypted = encrypt_access_token("gho_realtoken", monkeypatch_secret)
    already_expired = datetime.now(timezone.utc) - timedelta(seconds=1)
    await create_session(pool, "sess-expired", 42, "octocat", encrypted, already_expired)

    assert await get_session(pool, "sess-expired") is None


@pytest.mark.asyncio
async def test_logout_deletes_session_and_clears_cookie(pool, monkeypatch):
    monkeypatch.setenv("SESSION_SECRET", "test-session-secret")
    encrypted = encrypt_access_token("gho_realtoken", "test-session-secret")
    expires = datetime.now(timezone.utc) + timedelta(hours=1)
    await create_session(pool, "sess-logout", 42, "octocat", encrypted, expires)
    signed = sign_session_id("sess-logout", "test-session-secret")

    app.state.db_pool = pool
    transport = ASGITransport(app=app)
    async with AsyncClient(
        transport=transport, base_url="http://test", cookies={"session": signed}
    ) as client:
        logout_response = await client.get("/auth/logout", follow_redirects=False)

        assert logout_response.status_code == 307
        assert await get_session(pool, "sess-logout") is None

        # Pressing Back with the old cookie (still held by the client - a
        # real browser would send it too until the Set-Cookie expiry is
        # processed) must not resurrect the session server-side.
        picker_response = await client.get("/dashboard", follow_redirects=False)
    assert picker_response.status_code == 307
    assert picker_response.headers["location"] == "/"


def test_sign_and_unsign_round_trip():
    signed = sign_session_id("sess-123", "test-secret")
    assert unsign_session_id(signed, "test-secret") == "sess-123"


def test_unsign_rejects_tampered_value():
    signed = sign_session_id("sess-123", "test-secret")
    first, rest = signed.split(".", 1)
    tampered_first = ("a" if first[0] != "a" else "b") + first[1:]
    tampered = f"{tampered_first}.{rest}"
    assert unsign_session_id(tampered, "test-secret") is None


def test_signing_and_encryption_keys_derived_from_same_secret_are_independent():
    # Before this fix, both the session/oauth-state cookie signature and
    # the Fernet key encrypting stored GitHub tokens were derived straight
    # from SESSION_SECRET with no domain separation - reusing one secret
    # across a signing primitive and an encryption primitive is the kind
    # of key reuse that can let a weakness in either compromise both.
    signing_material = _derive_key("test-secret", b"aletheore-cookie-signing")
    encryption_material = _derive_key("test-secret", b"aletheore-token-encryption")
    assert signing_material != encryption_material
    assert _signing_secret("test-secret") != "test-secret"
    assert _fernet_key("test-secret") != _fernet_key("wrong-secret")


def test_safe_relative_next_path_accepted():
    assert _is_safe_next_path("/subscribe/claim") == "/subscribe/claim"


def test_missing_next_defaults_to_dashboard():
    assert _is_safe_next_path(None) == "/dashboard"


def test_absolute_next_url_rejected():
    assert _is_safe_next_path("https://evil.example.com/phish") == "/dashboard"


def test_protocol_relative_next_url_rejected():
    assert _is_safe_next_path("//evil.example.com/phish") == "/dashboard"


def test_next_path_not_starting_with_slash_rejected():
    assert _is_safe_next_path("evil.example.com") == "/dashboard"


@pytest.mark.asyncio
async def test_login_redirects_to_github_authorize(pool, monkeypatch):
    monkeypatch.setenv("GITHUB_CLIENT_ID", "test-client-id")
    monkeypatch.setenv("PUBLIC_BASE_URL", "http://test")
    app.state.db_pool = pool
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.get("/auth/login", follow_redirects=False)
    assert response.status_code == 307
    assert "github.com/login/oauth/authorize" in response.headers["location"]
    assert "client_id=test-client-id" in response.headers["location"]


@pytest.mark.asyncio
async def test_login_sets_next_cookie(pool, monkeypatch):
    monkeypatch.setenv("GITHUB_CLIENT_ID", "test-client-id")
    monkeypatch.setenv("PUBLIC_BASE_URL", "http://test")
    app.state.db_pool = pool
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.get("/auth/login?next=/subscribe/claim", follow_redirects=False)
    assert response.status_code == 307
    assert response.cookies.get("aletheore_oauth_next").strip('"') == "/subscribe/claim"


@pytest.mark.asyncio
async def test_callback_creates_session_and_sets_cookie(pool, monkeypatch):
    monkeypatch.setenv("SESSION_SECRET", "test-session-secret")

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/login/oauth/access_token":
            return httpx.Response(200, json={"access_token": "gho_faketoken"})
        if request.url.path == "/user":
            return httpx.Response(200, json={"id": 42, "login": "octocat"})
        return httpx.Response(404)

    monkeypatch.setattr(
        "app_server.auth._github_http_client",
        lambda: httpx.Client(transport=httpx.MockTransport(handler), base_url="https://api.github.com"),
    )
    monkeypatch.setattr(
        "app_server.auth._github_oauth_http_client",
        lambda: httpx.Client(transport=httpx.MockTransport(handler), base_url="https://github.com"),
    )

    app.state.db_pool = pool
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="https://test") as client:
        login_response = await client.get("/auth/login", follow_redirects=False)
        state = login_response.headers["location"].split("state=")[1]
        response = await client.get(
            f"/auth/callback?code=fake-code&state={state}", follow_redirects=False
        )

    assert response.status_code == 307
    assert "session" in response.cookies
    session_id = unsign_session_id(response.cookies["session"], "test-session-secret")
    row = await get_session(pool, session_id)
    assert row["github_login"] == "octocat"
    assert row["github_access_token"] != "gho_faketoken"


@pytest.mark.asyncio
async def test_callback_stores_refresh_token_when_github_returns_one(pool, monkeypatch):
    monkeypatch.setenv("SESSION_SECRET", "test-session-secret")

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/login/oauth/access_token":
            return httpx.Response(200, json={"access_token": "gho_faketoken", "refresh_token": "ghr_realrefresh"})
        if request.url.path == "/user":
            return httpx.Response(200, json={"id": 42, "login": "octocat"})
        return httpx.Response(404)

    monkeypatch.setattr(
        "app_server.auth._github_http_client",
        lambda: httpx.Client(transport=httpx.MockTransport(handler), base_url="https://api.github.com"),
    )
    monkeypatch.setattr(
        "app_server.auth._github_oauth_http_client",
        lambda: httpx.Client(transport=httpx.MockTransport(handler), base_url="https://github.com"),
    )

    app.state.db_pool = pool
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="https://test") as client:
        login_response = await client.get("/auth/login", follow_redirects=False)
        state = login_response.headers["location"].split("state=")[1]
        response = await client.get(
            f"/auth/callback?code=fake-code&state={state}", follow_redirects=False
        )

    session_id = unsign_session_id(response.cookies["session"], "test-session-secret")
    row = await get_session(pool, session_id)
    assert row["github_refresh_token"] != "ghr_realrefresh"  # stored encrypted, not raw
    assert row["github_refresh_token"] is not None


@pytest.mark.asyncio
async def test_callback_handles_missing_refresh_token(pool, monkeypatch):
    # GitHub only issues a refresh_token when the App has "Expire user
    # authorization tokens" enabled - absent otherwise, and sign-in must
    # not break just because this field is missing.
    monkeypatch.setenv("SESSION_SECRET", "test-session-secret")

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/login/oauth/access_token":
            return httpx.Response(200, json={"access_token": "gho_faketoken"})
        if request.url.path == "/user":
            return httpx.Response(200, json={"id": 42, "login": "octocat"})
        return httpx.Response(404)

    monkeypatch.setattr(
        "app_server.auth._github_http_client",
        lambda: httpx.Client(transport=httpx.MockTransport(handler), base_url="https://api.github.com"),
    )
    monkeypatch.setattr(
        "app_server.auth._github_oauth_http_client",
        lambda: httpx.Client(transport=httpx.MockTransport(handler), base_url="https://github.com"),
    )

    app.state.db_pool = pool
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="https://test") as client:
        login_response = await client.get("/auth/login", follow_redirects=False)
        state = login_response.headers["location"].split("state=")[1]
        response = await client.get(
            f"/auth/callback?code=fake-code&state={state}", follow_redirects=False
        )

    assert response.status_code == 307
    session_id = unsign_session_id(response.cookies["session"], "test-session-secret")
    row = await get_session(pool, session_id)
    assert row["github_refresh_token"] is None


@pytest.mark.asyncio
async def test_callback_rejects_mismatched_state(pool, monkeypatch):
    monkeypatch.setenv("SESSION_SECRET", "test-session-secret")
    app.state.db_pool = pool
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="https://test") as client:
        await client.get("/auth/login", follow_redirects=False)
        response = await client.get(
            "/auth/callback?code=fake-code&state=wrong-state", follow_redirects=False
        )
    assert response.status_code == 400


@pytest.mark.asyncio
async def test_callback_without_state_cookie_succeeds_for_app_install_flow(pool, monkeypatch):
    # GitHub App "Install" redirects straight to /auth/callback with
    # installation_id + code, never going through /auth/login first - so
    # there's no oauth_state cookie and often no state param at all. This
    # entry point must still work.
    monkeypatch.setenv("SESSION_SECRET", "test-session-secret")

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/login/oauth/access_token":
            return httpx.Response(200, json={"access_token": "gho_faketoken"})
        if request.url.path == "/user":
            return httpx.Response(200, json={"id": 42, "login": "octocat"})
        return httpx.Response(404)

    monkeypatch.setattr(
        "app_server.auth._github_http_client",
        lambda: httpx.Client(transport=httpx.MockTransport(handler), base_url="https://api.github.com"),
    )
    monkeypatch.setattr(
        "app_server.auth._github_oauth_http_client",
        lambda: httpx.Client(transport=httpx.MockTransport(handler), base_url="https://github.com"),
    )

    app.state.db_pool = pool
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="https://test") as client:
        response = await client.get(
            "/auth/callback?code=fake-code&installation_id=123&setup_action=install",
            follow_redirects=False,
        )

    assert response.status_code == 307
    assert "session" in response.cookies


@pytest.mark.asyncio
async def test_callback_direct_install_honors_state_as_next_path(pool, monkeypatch):
    # github_app_install_url() puts our own next_path in `state` for the
    # direct-install entry point (no oauth_state cookie, so state isn't a
    # CSRF nonce here) - the callback should redirect there instead of the
    # /dashboard default.
    monkeypatch.setenv("SESSION_SECRET", "test-session-secret")

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/login/oauth/access_token":
            return httpx.Response(200, json={"access_token": "gho_faketoken"})
        if request.url.path == "/user":
            return httpx.Response(200, json={"id": 42, "login": "octocat"})
        return httpx.Response(404)

    monkeypatch.setattr(
        "app_server.auth._github_http_client",
        lambda: httpx.Client(transport=httpx.MockTransport(handler), base_url="https://api.github.com"),
    )
    monkeypatch.setattr(
        "app_server.auth._github_oauth_http_client",
        lambda: httpx.Client(transport=httpx.MockTransport(handler), base_url="https://github.com"),
    )

    app.state.db_pool = pool
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="https://test") as client:
        response = await client.get(
            "/auth/callback?code=fake-code&installation_id=123&setup_action=install"
            "&state=%2Fsubscribe%3Fplan%3Dteam%26interval%3Dmonth",
            follow_redirects=False,
        )

    assert response.status_code == 307
    assert response.headers["location"] == "/subscribe?plan=team&interval=month"


@pytest.mark.asyncio
async def test_callback_with_installation_id_upserts_installation_synchronously(pool, monkeypatch):
    # /subscribe reads installations from our DB, which is normally populated
    # by the async `installation` webhook - a browser redirect straight back
    # from GitHub's install flow can land here before that webhook arrives.
    # The callback must upsert the row itself so /subscribe never sees a gap.
    monkeypatch.setenv("SESSION_SECRET", "test-session-secret")
    monkeypatch.setattr("app_server.auth.generate_app_jwt", lambda app_id, key: "fake-app-jwt")

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/login/oauth/access_token":
            return httpx.Response(200, json={"access_token": "gho_faketoken"})
        if request.url.path == "/user":
            return httpx.Response(200, json={"id": 42, "login": "octocat"})
        if request.url.path == "/app/installations/999":
            return httpx.Response(200, json={"id": 999, "account": {"login": "acme-corp"}})
        return httpx.Response(404)

    monkeypatch.setattr(
        "app_server.auth._github_http_client",
        lambda: httpx.Client(transport=httpx.MockTransport(handler), base_url="https://api.github.com"),
    )
    monkeypatch.setattr(
        "app_server.auth._github_oauth_http_client",
        lambda: httpx.Client(transport=httpx.MockTransport(handler), base_url="https://github.com"),
    )

    app.state.db_pool = pool
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="https://test") as client:
        response = await client.get(
            "/auth/callback?code=fake-code&installation_id=999&setup_action=install",
            follow_redirects=False,
        )

    assert response.status_code == 307
    installation = await get_installation(pool, 999)
    assert installation is not None
    assert installation["account_login"] == "acme-corp"


@pytest.mark.asyncio
async def test_callback_installation_upsert_failure_does_not_break_signin(pool, monkeypatch):
    # The synchronous upsert is best-effort - the webhook is still the
    # source of truth. If GitHub's /app/installations/{id} call fails (or
    # the JWT can't be generated), sign-in must still succeed.
    monkeypatch.setenv("SESSION_SECRET", "test-session-secret")

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/login/oauth/access_token":
            return httpx.Response(200, json={"access_token": "gho_faketoken"})
        if request.url.path == "/user":
            return httpx.Response(200, json={"id": 42, "login": "octocat"})
        return httpx.Response(404)

    monkeypatch.setattr(
        "app_server.auth._github_http_client",
        lambda: httpx.Client(transport=httpx.MockTransport(handler), base_url="https://api.github.com"),
    )
    monkeypatch.setattr(
        "app_server.auth._github_oauth_http_client",
        lambda: httpx.Client(transport=httpx.MockTransport(handler), base_url="https://github.com"),
    )

    app.state.db_pool = pool
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="https://test") as client:
        response = await client.get(
            "/auth/callback?code=fake-code&installation_id=998&setup_action=install",
            follow_redirects=False,
        )

    assert response.status_code == 307
    assert "session" in response.cookies
    assert await get_installation(pool, 998) is None


@pytest.mark.asyncio
async def test_get_current_session_decrypts_access_token(pool, monkeypatch):
    monkeypatch.setenv("SESSION_SECRET", "test-session-secret")
    encrypted = encrypt_access_token("gho_realtoken", "test-session-secret")
    expires = datetime.now(timezone.utc) + timedelta(hours=1)
    await create_session(pool, "sess-3", 42, "octocat", encrypted, expires)
    signed = sign_session_id("sess-3", "test-session-secret")

    class FakeRequest:
        cookies = {"session": signed}
        app = type("App", (), {"state": type("State", (), {"db_pool": pool})()})()

    session = await get_current_session(FakeRequest())
    assert session["github_access_token"] == "gho_realtoken"


@pytest.mark.asyncio
async def test_get_current_session_decrypts_refresh_token_when_present(pool, monkeypatch):
    monkeypatch.setenv("SESSION_SECRET", "test-session-secret")
    encrypted_access = encrypt_access_token("gho_realtoken", "test-session-secret")
    encrypted_refresh = encrypt_access_token("ghr_realrefresh", "test-session-secret")
    expires = datetime.now(timezone.utc) + timedelta(hours=1)
    await create_session(
        pool, "sess-4", 42, "octocat", encrypted_access, expires, refresh_token=encrypted_refresh
    )
    signed = sign_session_id("sess-4", "test-session-secret")

    class FakeRequest:
        cookies = {"session": signed}
        app = type("App", (), {"state": type("State", (), {"db_pool": pool})()})()

    session = await get_current_session(FakeRequest())
    assert session["github_refresh_token"] == "ghr_realrefresh"


@pytest.mark.asyncio
async def test_get_current_session_leaves_refresh_token_none_when_absent(pool, monkeypatch):
    monkeypatch.setenv("SESSION_SECRET", "test-session-secret")
    encrypted_access = encrypt_access_token("gho_realtoken", "test-session-secret")
    expires = datetime.now(timezone.utc) + timedelta(hours=1)
    await create_session(pool, "sess-5", 42, "octocat", encrypted_access, expires)
    signed = sign_session_id("sess-5", "test-session-secret")

    class FakeRequest:
        cookies = {"session": signed}
        app = type("App", (), {"state": type("State", (), {"db_pool": pool})()})()

    session = await get_current_session(FakeRequest())
    assert session["github_refresh_token"] is None


def test_refresh_github_access_token_returns_new_tokens(monkeypatch):
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/login/oauth/access_token"
        return httpx.Response(
            200, json={"access_token": "gho_newtoken", "refresh_token": "ghr_newrefresh"}
        )

    monkeypatch.setattr(
        "app_server.auth._github_oauth_http_client",
        lambda: httpx.Client(transport=httpx.MockTransport(handler), base_url="https://github.com"),
    )

    access_token, refresh_token = refresh_github_access_token("ghr_oldrefresh", "client-id", "client-secret")
    assert access_token == "gho_newtoken"
    assert refresh_token == "ghr_newrefresh"


def test_refresh_github_access_token_raises_when_github_reports_an_error(monkeypatch):
    # GitHub's refresh endpoint returns 200 with an `error` field for a
    # dead/revoked refresh_token, not a 4xx status.
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"error": "bad_refresh_token", "error_description": "expired"})

    monkeypatch.setattr(
        "app_server.auth._github_oauth_http_client",
        lambda: httpx.Client(transport=httpx.MockTransport(handler), base_url="https://github.com"),
    )

    with pytest.raises(RuntimeError):
        refresh_github_access_token("ghr_deadrefresh", "client-id", "client-secret")


def _mock_github_clients(monkeypatch, handler):
    monkeypatch.setattr(
        "app_server.auth._github_http_client",
        lambda: httpx.Client(transport=httpx.MockTransport(handler), base_url="https://api.github.com"),
    )
    monkeypatch.setattr(
        "app_server.auth._github_oauth_http_client",
        lambda: httpx.Client(transport=httpx.MockTransport(handler), base_url="https://github.com"),
    )


def _handler_with_emails(emails: list[dict]):
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/login/oauth/access_token":
            return httpx.Response(200, json={"access_token": "gho_faketoken"})
        if request.url.path == "/user":
            return httpx.Response(200, json={"id": 42, "login": "octocat"})
        if request.url.path == "/user/emails":
            return httpx.Response(200, json=emails)
        return httpx.Response(404)

    return handler


@pytest.mark.asyncio
async def test_callback_captures_email_and_enqueues_welcome_on_first_login(pool, monkeypatch):
    monkeypatch.setenv("SESSION_SECRET", "test-session-secret")
    _mock_github_clients(
        monkeypatch,
        _handler_with_emails(
            [
                {"email": "old@example.com", "primary": False, "verified": True},
                {"email": "octocat@example.com", "primary": True, "verified": True},
            ]
        ),
    )

    enqueue_calls = []
    monkeypatch.setattr(
        "app_server.auth.enqueue_transactional_email",
        lambda redis_url, **kwargs: enqueue_calls.append(kwargs),
    )

    app.state.db_pool = pool
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="https://test") as client:
        login_response = await client.get("/auth/login", follow_redirects=False)
        state = login_response.headers["location"].split("state=")[1]
        response = await client.get(
            f"/auth/callback?code=fake-code&state={state}", follow_redirects=False
        )

    assert response.status_code == 307
    row = await pool.fetchrow("SELECT email FROM github_user_emails WHERE github_login = 'octocat'")
    assert row["email"] == "octocat@example.com"

    assert len(enqueue_calls) == 1
    assert enqueue_calls[0]["dedupe_key"] == "welcome:octocat"
    assert enqueue_calls[0]["template_name"] == "welcome"
    assert enqueue_calls[0]["template_arg"] == "octocat"
    assert enqueue_calls[0]["to_email"] == "octocat@example.com"


@pytest.mark.asyncio
async def test_callback_does_not_reenqueue_welcome_on_second_login(pool, monkeypatch):
    monkeypatch.setenv("SESSION_SECRET", "test-session-secret")
    _mock_github_clients(
        monkeypatch,
        _handler_with_emails([{"email": "octocat@example.com", "primary": True, "verified": True}]),
    )

    enqueue_calls = []
    monkeypatch.setattr(
        "app_server.auth.enqueue_transactional_email",
        lambda redis_url, **kwargs: enqueue_calls.append(kwargs),
    )

    app.state.db_pool = pool
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="https://test") as client:
        for _ in range(2):
            login_response = await client.get("/auth/login", follow_redirects=False)
            state = login_response.headers["location"].split("state=")[1]
            await client.get(f"/auth/callback?code=fake-code&state={state}", follow_redirects=False)

    assert len(enqueue_calls) == 1


@pytest.mark.asyncio
async def test_callback_degrades_gracefully_when_email_permission_not_granted(pool, monkeypatch):
    # The GitHub App's "Email addresses" account permission (see auth.py's
    # _fetch_primary_verified_email comment) not yet granted for this user
    # surfaces as a 403 on /user/emails - sign-in must still succeed.
    monkeypatch.setenv("SESSION_SECRET", "test-session-secret")

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/login/oauth/access_token":
            return httpx.Response(200, json={"access_token": "gho_faketoken"})
        if request.url.path == "/user":
            return httpx.Response(200, json={"id": 42, "login": "octocat"})
        if request.url.path == "/user/emails":
            return httpx.Response(403, json={"message": "missing permission"})
        return httpx.Response(404)

    _mock_github_clients(monkeypatch, handler)

    app.state.db_pool = pool
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="https://test") as client:
        login_response = await client.get("/auth/login", follow_redirects=False)
        state = login_response.headers["location"].split("state=")[1]
        response = await client.get(
            f"/auth/callback?code=fake-code&state={state}", follow_redirects=False
        )

    assert response.status_code == 307
    row = await pool.fetchrow("SELECT 1 FROM github_user_emails WHERE github_login = 'octocat'")
    assert row is None


@pytest.mark.asyncio
async def test_login_rate_limits_after_threshold(pool, monkeypatch, redis_conn):
    import app_server.auth as auth_module
    from app_server.rate_limit import is_rate_limited as real_is_rate_limited

    monkeypatch.setattr(auth_module, "is_rate_limited", real_is_rate_limited)
    monkeypatch.setattr(auth_module, "AUTH_RATE_LIMIT", 2)
    monkeypatch.setattr("redis.Redis.from_url", lambda url: redis_conn)

    app.state.db_pool = pool
    transport = ASGITransport(app=app)
    headers = {"x-forwarded-for": "203.0.113.60"}
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        first = await client.get("/auth/login", headers=headers, follow_redirects=False)
        second = await client.get("/auth/login", headers=headers, follow_redirects=False)
        third = await client.get("/auth/login", headers=headers, follow_redirects=False)

    assert first.status_code == 307
    assert second.status_code == 307
    assert third.status_code == 429
    assert "retry-after" in third.headers


@pytest.mark.asyncio
async def test_auth_rate_limit_is_shared_between_login_and_callback(pool, monkeypatch, redis_conn):
    # Same logical flow, one budget - a separate limit per endpoint would
    # just double an attacker's effective allowance.
    import app_server.auth as auth_module
    from app_server.rate_limit import is_rate_limited as real_is_rate_limited

    monkeypatch.setattr(auth_module, "is_rate_limited", real_is_rate_limited)
    monkeypatch.setattr(auth_module, "AUTH_RATE_LIMIT", 1)
    monkeypatch.setattr("redis.Redis.from_url", lambda url: redis_conn)

    app.state.db_pool = pool
    transport = ASGITransport(app=app)
    headers = {"x-forwarded-for": "203.0.113.61"}
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        login_response = await client.get("/auth/login", headers=headers, follow_redirects=False)
        callback_response = await client.get(
            "/auth/callback?code=fake-code", headers=headers, follow_redirects=False
        )

    assert login_response.status_code == 307
    assert callback_response.status_code == 429


@pytest.mark.asyncio
async def test_auth_rate_limit_is_keyed_per_ip(pool, monkeypatch, redis_conn):
    import app_server.auth as auth_module
    from app_server.rate_limit import is_rate_limited as real_is_rate_limited

    monkeypatch.setattr(auth_module, "is_rate_limited", real_is_rate_limited)
    monkeypatch.setattr(auth_module, "AUTH_RATE_LIMIT", 1)
    monkeypatch.setattr("redis.Redis.from_url", lambda url: redis_conn)

    app.state.db_pool = pool
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        first = await client.get(
            "/auth/login", headers={"x-forwarded-for": "1.1.1.1"}, follow_redirects=False
        )
        second_same_ip = await client.get(
            "/auth/login", headers={"x-forwarded-for": "1.1.1.1"}, follow_redirects=False
        )
        first_other_ip = await client.get(
            "/auth/login", headers={"x-forwarded-for": "2.2.2.2"}, follow_redirects=False
        )

    assert first.status_code == 307
    assert second_same_ip.status_code == 429
    assert first_other_ip.status_code == 307


@pytest.mark.asyncio
async def test_login_fails_open_when_redis_is_unreachable(pool, monkeypatch):
    import app_server.auth as auth_module
    from app_server.rate_limit import is_rate_limited as real_is_rate_limited

    monkeypatch.setattr(auth_module, "is_rate_limited", real_is_rate_limited)

    def _boom(url):
        raise ConnectionError("redis unreachable")

    monkeypatch.setattr("redis.Redis.from_url", _boom)

    app.state.db_pool = pool
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.get("/auth/login", follow_redirects=False)

    assert response.status_code == 307
