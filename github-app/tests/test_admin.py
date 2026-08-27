import asyncio
import socket
import threading
import time
from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock, patch

import httpx
import pytest
from fastapi import HTTPException
from httpx import ASGITransport, AsyncClient

from app_server.admin import (
    _fetch_administered_installation_ids,
    _administered_installation_ids_for_session_or_401,
    _build_updated_seat_items,
    _has_real_admin_permission,
    _looks_like_email,
    _repo_installation_id,
)
from app_server.auth import decrypt_access_token, encrypt_access_token, sign_session_id
from app_server.url_validation import UnsafeURLError
from app_server import admin
from app_server.db import (
    add_paddle_ids_to_installation,
    create_session,
    get_max_tokens,
    get_session,
    insert_repo_history,
    is_installation_member,
    record_llm_spend,
    set_installation_plan,
    upsert_installation,
)
from app_server.main import app
from app_server.paddle_client import PaddleAPIError
from app_server.paddle_pricing import EXTRA_SEAT_PRICE_ID


async def _logged_in_client(pool, monkeypatch, installation_id=100, plan="air"):
    monkeypatch.setenv("SESSION_SECRET", "test-session-secret")
    await upsert_installation(pool, installation_id, "octocat")
    await set_installation_plan(pool, installation_id, plan)
    await insert_repo_history(
        pool,
        installation_id,
        "octocat/hello-world",
        datetime.now(timezone.utc),
        {"scanned_at": "x"},
    )
    await create_session(
        pool,
        "sess-1",
        42,
        "octocat",
        encrypt_access_token("gho_faketoken", "test-session-secret"),
        datetime.now(timezone.utc) + timedelta(hours=1),
    )

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={"total_count": 1, "installations": [{"id": installation_id}]},
        )

    monkeypatch.setattr(
        "app_server.admin._github_http_client",
        lambda: httpx.Client(transport=httpx.MockTransport(handler), base_url="https://api.github.com"),
    )
    # Deterministic, network-free DNS answer for URL-validation tests that
    # don't care about SSRF behavior specifically - a real public address.
    monkeypatch.setattr(
        "app_server.url_validation.socket.getaddrinfo",
        lambda *a, **k: [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("93.184.216.34", 443))],
    )
    # Default "logged in" represents a real GitHub admin on the repo -
    # _has_real_admin_permission would otherwise attempt a live GitHub API
    # call (via app_server.github_auth, a different client than the
    # coarse-check mock above) and fail closed. Tests exercising the
    # narrower, non-admin case override this back to _async_false after
    # calling this fixture.
    monkeypatch.setattr("app_server.admin._has_real_admin_permission", _async_true)

    app.state.db_pool = pool
    signed = sign_session_id("sess-1", "test-session-secret")
    transport = ASGITransport(app=app)
    return AsyncClient(transport=transport, base_url="http://test", cookies={"session": signed})


async def _async_true(*args, **kwargs) -> bool:
    return True


async def _async_false(*args, **kwargs) -> bool:
    return False


async def _mock_github_installations(monkeypatch, installation_ids: list[int]):
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "total_count": len(installation_ids),
                "installations": [{"id": installation_id} for installation_id in installation_ids],
            },
        )

    monkeypatch.setattr(
        "app_server.admin._github_http_client",
        lambda: httpx.Client(transport=httpx.MockTransport(handler), base_url="https://api.github.com"),
    )


async def _create_session_with_tokens(
    pool, monkeypatch, session_id, access_token, refresh_token=None
):
    monkeypatch.setenv("SESSION_SECRET", "test-session-secret")
    await create_session(
        pool,
        session_id,
        42,
        "octocat",
        encrypt_access_token(access_token, "test-session-secret"),
        datetime.now(timezone.utc) + timedelta(hours=1),
        refresh_token=encrypt_access_token(refresh_token, "test-session-secret") if refresh_token else None,
    )
    return {
        "id": session_id,
        "github_login": "octocat",
        "github_access_token": access_token,
        "github_refresh_token": refresh_token,
    }


def test_fetch_administered_installation_ids_collects_paginated_results(monkeypatch):
    installation_ids = list(range(1000, 1101))
    seen_params = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen_params.append(dict(request.url.params))
        page = int(request.url.params["page"])
        assert request.url.params["per_page"] == "100"
        start = (page - 1) * 100
        return httpx.Response(
            200,
            json={
                "total_count": len(installation_ids),
                "installations": [{"id": installation_id} for installation_id in installation_ids[start:start + 100]],
            },
            request=request,
        )

    monkeypatch.setattr(
        "app_server.admin._github_http_client",
        lambda: httpx.Client(transport=httpx.MockTransport(handler), base_url="https://api.github.com"),
    )

    assert _fetch_administered_installation_ids("gho_many") == set(installation_ids)
    assert seen_params == [{"per_page": "100", "page": "1"}, {"per_page": "100", "page": "2"}]


@pytest.mark.asyncio
async def test_administered_ids_for_session_succeeds_without_refresh_when_token_valid(pool, monkeypatch):
    session = await _create_session_with_tokens(pool, monkeypatch, "sess-valid", "gho_valid")

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.headers["Authorization"] == "Bearer gho_valid"
        return httpx.Response(200, json={"installations": [{"id": 1}, {"id": 2}]})

    monkeypatch.setattr(
        "app_server.admin._github_http_client",
        lambda: httpx.Client(transport=httpx.MockTransport(handler), base_url="https://api.github.com"),
    )

    ids = await _administered_installation_ids_for_session_or_401(pool, session)
    assert ids == {1, 2}
    # Untouched - no refresh was needed.
    row = await get_session(pool, "sess-valid")
    assert decrypt_access_token(row["github_access_token"], "test-session-secret") == "gho_valid"


@pytest.mark.asyncio
async def test_administered_ids_for_session_refreshes_and_retries_on_401(pool, monkeypatch):
    session = await _create_session_with_tokens(
        pool, monkeypatch, "sess-refresh", "gho_dead", refresh_token="ghr_stillgood"
    )

    def handler(request: httpx.Request) -> httpx.Response:
        if request.headers["Authorization"] == "Bearer gho_dead":
            return httpx.Response(401, json={"message": "Bad credentials"})
        if request.headers["Authorization"] == "Bearer gho_fresh":
            return httpx.Response(200, json={"installations": [{"id": 7}]})
        raise AssertionError("unexpected token")

    monkeypatch.setattr(
        "app_server.admin._github_http_client",
        lambda: httpx.Client(transport=httpx.MockTransport(handler), base_url="https://api.github.com"),
    )

    def fake_refresh(refresh_token, client_id, client_secret):
        assert refresh_token == "ghr_stillgood"
        return "gho_fresh", "ghr_rotated"

    monkeypatch.setattr("app_server.admin.refresh_github_access_token", fake_refresh)

    ids = await _administered_installation_ids_for_session_or_401(pool, session)
    assert ids == {7}

    row = await get_session(pool, "sess-refresh")
    assert decrypt_access_token(row["github_access_token"], "test-session-secret") == "gho_fresh"
    assert decrypt_access_token(row["github_refresh_token"], "test-session-secret") == "ghr_rotated"


@pytest.mark.asyncio
async def test_administered_ids_for_session_keeps_old_refresh_token_if_github_omits_a_new_one(
    pool, monkeypatch
):
    session = await _create_session_with_tokens(
        pool, monkeypatch, "sess-norotate", "gho_dead", refresh_token="ghr_stillgood"
    )

    def handler(request: httpx.Request) -> httpx.Response:
        if request.headers["Authorization"] == "Bearer gho_dead":
            return httpx.Response(401, json={"message": "Bad credentials"})
        return httpx.Response(200, json={"installations": [{"id": 7}]})

    monkeypatch.setattr(
        "app_server.admin._github_http_client",
        lambda: httpx.Client(transport=httpx.MockTransport(handler), base_url="https://api.github.com"),
    )
    monkeypatch.setattr(
        "app_server.admin.refresh_github_access_token",
        lambda refresh_token, client_id, client_secret: ("gho_fresh", None),
    )

    await _administered_installation_ids_for_session_or_401(pool, session)

    row = await get_session(pool, "sess-norotate")
    assert decrypt_access_token(row["github_refresh_token"], "test-session-secret") == "ghr_stillgood"


@pytest.mark.asyncio
async def test_administered_ids_for_session_deletes_session_when_no_refresh_token(pool, monkeypatch):
    session = await _create_session_with_tokens(pool, monkeypatch, "sess-norefresh", "gho_dead")

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(401, json={"message": "Bad credentials"})

    monkeypatch.setattr(
        "app_server.admin._github_http_client",
        lambda: httpx.Client(transport=httpx.MockTransport(handler), base_url="https://api.github.com"),
    )

    with pytest.raises(HTTPException) as exc_info:
        await _administered_installation_ids_for_session_or_401(pool, session)
    assert exc_info.value.status_code == 401
    assert await get_session(pool, "sess-norefresh") is None


@pytest.mark.asyncio
async def test_administered_ids_for_session_deletes_session_when_refresh_fails(pool, monkeypatch, caplog):
    session = await _create_session_with_tokens(
        pool, monkeypatch, "sess-refreshfails", "gho_dead", refresh_token="ghr_alsodead"
    )

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(401, json={"message": "Bad credentials"})

    monkeypatch.setattr(
        "app_server.admin._github_http_client",
        lambda: httpx.Client(transport=httpx.MockTransport(handler), base_url="https://api.github.com"),
    )

    def fake_refresh(refresh_token, client_id, client_secret):
        raise RuntimeError("bad_refresh_token")

    monkeypatch.setattr("app_server.admin.refresh_github_access_token", fake_refresh)

    # Before this fix, a failed refresh attempt (dead refresh_token, GitHub
    # outage, unexpected response shape) was swallowed with a bare `except
    # Exception: return None` - nothing distinguished it in logs from the
    # unremarkable "no refresh_token on file" case.
    with caplog.at_level("WARNING", logger="app_server.admin"):
        with pytest.raises(HTTPException) as exc_info:
            await _administered_installation_ids_for_session_or_401(pool, session)
    assert exc_info.value.status_code == 401
    assert await get_session(pool, "sess-refreshfails") is None
    assert any("token refresh failed" in record.message for record in caplog.records)


@pytest.mark.asyncio
async def test_administered_ids_for_session_propagates_502_without_deleting_session(pool, monkeypatch):
    session = await _create_session_with_tokens(pool, monkeypatch, "sess-outage", "gho_valid")

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(500, json={"message": "internal error"})

    monkeypatch.setattr(
        "app_server.admin._github_http_client",
        lambda: httpx.Client(transport=httpx.MockTransport(handler), base_url="https://api.github.com"),
    )

    with pytest.raises(HTTPException) as exc_info:
        await _administered_installation_ids_for_session_or_401(pool, session)
    assert exc_info.value.status_code == 502
    assert await get_session(pool, "sess-outage") is not None


@pytest.mark.asyncio
async def test_admin_page_requires_login(pool):
    app.state.db_pool = pool
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.get("/admin/octocat/hello-world")
    assert response.status_code == 401


@pytest.mark.asyncio
async def test_admin_page_rejects_free_plan(pool, monkeypatch):
    client = await _logged_in_client(pool, monkeypatch, plan="free")
    async with client:
        response = await client.get("/admin/octocat/hello-world")
    assert response.status_code == 402


@pytest.mark.asyncio
async def test_admin_page_surfaces_llm_spend_and_flash_review_usage(pool, monkeypatch):
    # llm_spend and flash_review_monthly_count were already tracked
    # internally for the hard spend cap - this is the first place a
    # customer actually sees what their AI review usage is costing them.
    client = await _logged_in_client(pool, monkeypatch, plan="air")
    await record_llm_spend(pool, 100, 4.20)
    await pool.execute(
        """
        INSERT INTO flash_review_monthly_count (installation_id, month, review_count)
        VALUES ($1, date_trunc('month', now())::date, $2)
        """,
        100,
        7,
    )

    async with client:
        response = await client.get("/admin/octocat/hello-world")

    assert response.status_code == 200
    body = response.json()
    assert body["llm_spend_month_to_date"] == 4.20
    assert body["flash_reviews_month_to_date"] == 7
    assert body["llm_spend_cap"] > 0


@pytest.mark.asyncio
async def test_admin_page_reports_zero_usage_before_any_spend(pool, monkeypatch):
    client = await _logged_in_client(pool, monkeypatch, plan="air")

    async with client:
        response = await client.get("/admin/octocat/hello-world")

    assert response.status_code == 200
    body = response.json()
    assert body["llm_spend_month_to_date"] == 0.0
    assert body["flash_reviews_month_to_date"] == 0


@pytest.mark.asyncio
async def test_generate_token_returns_raw_value_once(pool, monkeypatch):
    client = await _logged_in_client(pool, monkeypatch)
    async with client:
        response = await client.post("/admin/octocat/hello-world/tokens", json={"label": "laptop"})
    assert response.status_code == 200


@pytest.mark.asyncio
async def test_generate_token_returns_the_id_create_api_token_actually_created(pool, monkeypatch):
    # Real incident this guards: generate_token used to discard
    # create_api_token's own RETURNING id and re-derive it via
    # list_api_tokens(...)[0]["id"] - an assumption that breaks under
    # concurrent token creation for the same installation, since a second
    # caller's newer row could sort first. Now that generate_token calls
    # create_api_token_within_limit directly (also closing the separate
    # count-then-insert race over the token limit), there's no second query
    # left to race - this asserts the id it returns is used verbatim.
    client = await _logged_in_client(pool, monkeypatch)

    async def fake_create_api_token_within_limit(pool, installation_id, token_hash, label, created_by, limit):
        return 42

    monkeypatch.setattr(
        "app_server.admin.create_api_token_within_limit", fake_create_api_token_within_limit
    )

    recorded_actions = []

    async def fake_record_admin_action(pool, installation_id, github_login, action, details):
        recorded_actions.append((action, details))

    monkeypatch.setattr("app_server.admin.record_admin_action", fake_record_admin_action)

    async with client:
        response = await client.post("/admin/octocat/hello-world/tokens", json={"label": "laptop"})

    assert response.status_code == 200
    assert response.json()["id"] == 42
    assert recorded_actions[-1][1]["token_id"] == 42
    assert len(response.json()["token"]) > 20


@pytest.mark.asyncio
async def test_generate_token_returns_422_for_missing_label(pool, monkeypatch):
    client = await _logged_in_client(pool, monkeypatch)
    async with client:
        response = await client.post("/admin/octocat/hello-world/tokens", json={})
    assert response.status_code == 422


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "bad_label",
    ["", "x" * 101, "bad\nlabel", "bad\ttab", "bad\x00null"],
)
async def test_generate_token_rejects_invalid_labels(pool, monkeypatch, bad_label):
    client = await _logged_in_client(pool, monkeypatch)
    async with client:
        response = await client.post("/admin/octocat/hello-world/tokens", json={"label": bad_label})
    assert response.status_code == 422


@pytest.mark.asyncio
async def test_set_webhook_url(pool, monkeypatch):
    client = await _logged_in_client(pool, monkeypatch)
    async with client:
        response = await client.put(
            "/admin/octocat/hello-world/webhook-url",
            json={"webhook_url": "https://hooks.slack.com/services/x"},
        )
    assert response.status_code == 200


@pytest.mark.parametrize(
    "value,expected",
    [
        ("ops@example.com", True),
        ("ops@sub.example.com", True),
        ("not-an-email", False),
        ("", False),
        ("@example.com", False),
        ("ops@", False),
        ("ops@example", False),
        ("ops@.com", False),
        ("ops@example.", False),
        ("ops has spaces@example.com", False),
        ("a@b@c.com", False),
    ],
)
def test_looks_like_email(value, expected):
    assert _looks_like_email(value) is expected


def test_looks_like_email_rejects_a_pathological_input_quickly():
    # Regression test for a real CodeQL-flagged, empirically-confirmed
    # ReDoS: the regex this replaced (`^[^@\s]+@[^@\s]+\.[^@\s]+$`) took
    # ~20 seconds to reject a 100KB crafted string on this Python version,
    # scaling quadratically. _looks_like_email is plain string operations
    # with no backtracking, so this must stay fast regardless of input
    # shape or size.
    #
    # Aletheore's own Flash Review caught a real bug in an earlier version
    # of this test: without the trailing space, the payload actually
    # *matches* the old regex (via the second-to-last dot as the "@...\."
    # separator and the final dot as the one-character suffix) - fast, not
    # slow, and accepted rather than rejected. A trailing space is required
    # so no split of the string can ever satisfy the old regex's final
    # [^@\s]+$, forcing it to exhaust every '@'/'.' combination before
    # giving up.
    payload = "!@!." + ("!." * 50_000) + " "
    start = time.monotonic()
    result = _looks_like_email(payload)
    elapsed = time.monotonic() - start
    assert result is False
    assert elapsed < 1.0


@pytest.mark.asyncio
async def test_set_alert_email(pool, monkeypatch):
    client = await _logged_in_client(pool, monkeypatch)
    async with client:
        response = await client.put(
            "/admin/octocat/hello-world/alert-email",
            json={"alert_email": "ops@example.com"},
        )
    assert response.status_code == 200


@pytest.mark.asyncio
async def test_set_alert_email_rejects_malformed_address(pool, monkeypatch):
    client = await _logged_in_client(pool, monkeypatch)
    async with client:
        response = await client.put(
            "/admin/octocat/hello-world/alert-email",
            json={"alert_email": "not-an-email"},
        )
    assert response.status_code == 400


@pytest.mark.asyncio
async def test_send_test_alert_email_requires_a_saved_address(pool, monkeypatch):
    client = await _logged_in_client(pool, monkeypatch)
    async with client:
        response = await client.post("/admin/octocat/hello-world/alert-email/test")
    assert response.status_code == 400


@pytest.mark.asyncio
async def test_saved_alert_email_is_reflected_back_on_the_admin_page(pool, monkeypatch):
    # Regression test for a real bug found while building this: get_installation
    # (app_server/db.py) used an explicit column list that didn't include the
    # new alert_email column, so a save would silently never be visible to
    # the settings page (or to test_alert_email_route's own "is one
    # configured?" check) despite the UPDATE itself succeeding.
    client = await _logged_in_client(pool, monkeypatch)
    async with client:
        await client.put(
            "/admin/octocat/hello-world/alert-email",
            json={"alert_email": "ops@example.com"},
        )
        response = await client.get("/admin/octocat/hello-world")
    assert response.status_code == 200
    assert response.json()["installation"]["alert_email"] == "ops@example.com"


@pytest.mark.asyncio
async def test_send_test_alert_email_enqueues_to_saved_address(pool, monkeypatch):
    client = await _logged_in_client(pool, monkeypatch)
    enqueued = []

    def fake_enqueue(*args, **kwargs):
        enqueued.append((args, kwargs))

    monkeypatch.setattr("app_server.admin.enqueue_transactional_email", fake_enqueue)
    async with client:
        put_response = await client.put(
            "/admin/octocat/hello-world/alert-email",
            json={"alert_email": "ops@example.com"},
        )
        assert put_response.status_code == 200
        response = await client.post("/admin/octocat/hello-world/alert-email/test")

    assert response.status_code == 200
    assert len(enqueued) == 1
    _args, kwargs = enqueued[0]
    assert kwargs["to_email"] == "ops@example.com"
    assert kwargs["template_name"] == "health_alert"


@pytest.mark.asyncio
async def test_set_pushover_user_key(pool, monkeypatch):
    client = await _logged_in_client(pool, monkeypatch)
    async with client:
        response = await client.put(
            "/admin/octocat/hello-world/pushover-user-key",
            json={"pushover_user_key": "u" * 30},
        )
    assert response.status_code == 200


@pytest.mark.asyncio
async def test_set_pushover_user_key_rejects_malformed_key(pool, monkeypatch):
    client = await _logged_in_client(pool, monkeypatch)
    async with client:
        response = await client.put(
            "/admin/octocat/hello-world/pushover-user-key",
            json={"pushover_user_key": "too-short"},
        )
    assert response.status_code == 400


@pytest.mark.asyncio
async def test_set_pushover_user_key_rejects_a_trailing_newline(pool, monkeypatch):
    # Regression: the validation regex used a bare $ instead of \Z -
    # without re.MULTILINE, $ matches either the true end of the string OR
    # right before a single trailing newline, so 30 valid characters plus
    # a trailing "\n" incorrectly passed. Confirmed directly before fixing.
    client = await _logged_in_client(pool, monkeypatch)
    async with client:
        response = await client.put(
            "/admin/octocat/hello-world/pushover-user-key",
            json={"pushover_user_key": "u" * 30 + "\n"},
        )
    assert response.status_code == 400


@pytest.mark.asyncio
async def test_send_test_pushover_requires_a_saved_key(pool, monkeypatch):
    monkeypatch.setenv("PUSHOVER_API_TOKEN", "server-app-token")
    from app_server.config import get_settings

    get_settings.cache_clear()
    client = await _logged_in_client(pool, monkeypatch)
    async with client:
        response = await client.post("/admin/octocat/hello-world/pushover-user-key/test")
    assert response.status_code == 400


@pytest.mark.asyncio
async def test_send_test_pushover_requires_server_token_configured(pool, monkeypatch):
    # PUSHOVER_API_TOKEN is Aletheore's own credential, not something any
    # one installation controls - if the founder hasn't configured it
    # server-wide yet, every installation's test click must fail with a
    # clear reason, not a raw exception from send_pushover_alert.
    monkeypatch.delenv("PUSHOVER_API_TOKEN", raising=False)
    from app_server.config import get_settings

    get_settings.cache_clear()
    client = await _logged_in_client(pool, monkeypatch)
    async with client:
        await client.put(
            "/admin/octocat/hello-world/pushover-user-key",
            json={"pushover_user_key": "u" * 30},
        )
        response = await client.post("/admin/octocat/hello-world/pushover-user-key/test")
    assert response.status_code == 400


@pytest.mark.asyncio
async def test_saved_pushover_user_key_is_reflected_back_on_the_admin_page(pool, monkeypatch):
    # Same class of bug as alert_email's regression test above: an explicit
    # SELECT column list in get_installation that doesn't include the new
    # column means a save is silently invisible everywhere that reads the
    # installation dict, despite the UPDATE itself succeeding.
    client = await _logged_in_client(pool, monkeypatch)
    async with client:
        await client.put(
            "/admin/octocat/hello-world/pushover-user-key",
            json={"pushover_user_key": "u" * 30},
        )
        response = await client.get("/admin/octocat/hello-world")
    assert response.status_code == 200
    assert response.json()["installation"]["pushover_user_key"] == "u" * 30


@pytest.mark.asyncio
async def test_send_test_pushover_sends_to_saved_key(pool, monkeypatch):
    monkeypatch.setenv("PUSHOVER_API_TOKEN", "server-app-token")
    from app_server.config import get_settings

    get_settings.cache_clear()
    client = await _logged_in_client(pool, monkeypatch)
    sent = []
    monkeypatch.setattr(
        "app_server.admin.send_pushover_alert",
        lambda token, user_key, message, **k: sent.append((token, user_key, message)),
    )
    async with client:
        put_response = await client.put(
            "/admin/octocat/hello-world/pushover-user-key",
            json={"pushover_user_key": "u" * 30},
        )
        assert put_response.status_code == 200
        response = await client.post("/admin/octocat/hello-world/pushover-user-key/test")

    assert response.status_code == 200
    assert len(sent) == 1
    token, user_key, _message = sent[0]
    assert token == "server-app-token"
    assert user_key == "u" * 30


@pytest.mark.asyncio
async def test_docs_repo_commit_defaults_to_disabled(pool, monkeypatch):
    client = await _logged_in_client(pool, monkeypatch)
    async with client:
        response = await client.get("/admin/octocat/hello-world/docs-repo-commit")
    assert response.status_code == 200
    assert response.json() == {"enabled": False, "pr_number": None}


@pytest.mark.asyncio
async def test_set_docs_repo_commit_enabled_persists(pool, monkeypatch):
    client = await _logged_in_client(pool, monkeypatch)
    async with client:
        put_response = await client.put(
            "/admin/octocat/hello-world/docs-repo-commit", json={"enabled": True}
        )
        assert put_response.status_code == 200
        get_response = await client.get("/admin/octocat/hello-world/docs-repo-commit")
    assert get_response.json()["enabled"] is True


@pytest.mark.asyncio
async def test_set_docs_repo_commit_can_be_disabled_again(pool, monkeypatch):
    client = await _logged_in_client(pool, monkeypatch)
    async with client:
        await client.put("/admin/octocat/hello-world/docs-repo-commit", json={"enabled": True})
        await client.put("/admin/octocat/hello-world/docs-repo-commit", json={"enabled": False})
        get_response = await client.get("/admin/octocat/hello-world/docs-repo-commit")
    assert get_response.json()["enabled"] is False


@pytest.mark.asyncio
async def test_send_test_notification_requires_a_saved_webhook(pool, monkeypatch):
    client = await _logged_in_client(pool, monkeypatch)
    async with client:
        response = await client.post("/admin/octocat/hello-world/webhook-url/test")
    assert response.status_code == 400


@pytest.mark.asyncio
async def test_send_test_notification_posts_to_saved_webhook(pool, monkeypatch):
    client = await _logged_in_client(pool, monkeypatch)
    sent = []

    def fake_send_health_alert(webhook_url, message, http_client=None):
        sent.append((webhook_url, message))

    monkeypatch.setattr("scan_worker.slack.send_health_alert", fake_send_health_alert)
    async with client:
        put_response = await client.put(
            "/admin/octocat/hello-world/webhook-url",
            json={"webhook_url": "https://hooks.slack.com/services/x"},
        )
        assert put_response.status_code == 200
        response = await client.post("/admin/octocat/hello-world/webhook-url/test")

    assert response.status_code == 200
    assert len(sent) == 1
    assert sent[0][0] == "https://hooks.slack.com/services/x"
    assert "octocat/hello-world" in sent[0][1]["text"]


@pytest.mark.asyncio
async def test_send_test_notification_reports_delivery_failure(pool, monkeypatch):
    client = await _logged_in_client(pool, monkeypatch)

    def fake_send_health_alert(webhook_url, message, http_client=None):
        raise httpx.HTTPStatusError(
            "internal secret detail that must never reach the caller",
            request=None,
            response=httpx.Response(502),
        )

    monkeypatch.setattr("scan_worker.slack.send_health_alert", fake_send_health_alert)
    async with client:
        await client.put(
            "/admin/octocat/hello-world/webhook-url",
            json={"webhook_url": "https://hooks.slack.com/services/x"},
        )
        response = await client.post("/admin/octocat/hello-world/webhook-url/test")

    assert response.status_code == 502
    # The raw exception message is an SSRF oracle (distinguishes refused
    # vs timed out vs an actual response from whatever the URL resolved
    # to) - must never be echoed back to the caller.
    assert "internal secret detail" not in response.text


@pytest.mark.asyncio
async def test_send_test_notification_revalidates_the_url_right_before_fetching(pool, monkeypatch):
    # Same DNS-rebinding defense as the health-check sweep: a URL that
    # validated fine when saved could resolve somewhere unsafe by the
    # time "test" is clicked. This must be caught here too, not just at
    # save time.
    client = await _logged_in_client(pool, monkeypatch)

    def fake_send_health_alert(webhook_url, message, http_client=None):
        raise AssertionError("must not fetch a webhook URL that just failed revalidation")

    monkeypatch.setattr("scan_worker.slack.send_health_alert", fake_send_health_alert)
    async with client:
        await client.put(
            "/admin/octocat/hello-world/webhook-url",
            json={"webhook_url": "https://hooks.slack.com/services/x"},
        )
        monkeypatch.setattr(
            "app_server.admin.validate_external_https_url",
            MagicMock(side_effect=UnsafeURLError("now resolves internally")),
        )
        response = await client.post("/admin/octocat/hello-world/webhook-url/test")

    assert response.status_code == 400


@pytest.mark.asyncio
async def test_send_test_notification_requires_login(pool):
    app.state.db_pool = pool
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.post("/admin/octocat/hello-world/webhook-url/test")
    assert response.status_code == 401


@pytest.mark.asyncio
async def test_set_public_status_route_toggles_the_flag(pool, monkeypatch):
    client = await _logged_in_client(pool, monkeypatch)
    async with client:
        on_response = await client.put(
            "/admin/octocat/hello-world/public-status", json={"enabled": True}
        )
        dashboard_response = await client.get("/admin/octocat/hello-world")
    assert on_response.status_code == 200
    assert on_response.json()["public_status_enabled"] is True
    assert dashboard_response.json()["public_status_enabled"] is True


@pytest.mark.asyncio
async def test_set_public_status_route_does_not_leak_to_other_repos(pool, monkeypatch):
    # F21: this route is repo-scoped in its URL and docstring, but used to
    # write an account-wide column - enabling it on one repo silently
    # exposed every other repo in the account, including private ones.
    client = await _logged_in_client(pool, monkeypatch)
    async with client:
        await client.put("/admin/octocat/hello-world/public-status", json={"enabled": True})
        other_repo_settings = await client.get("/admin/octocat/internal-billing")
    assert other_repo_settings.json()["public_status_enabled"] is False


@pytest.mark.asyncio
async def test_set_public_status_route_requires_login(pool):
    app.state.db_pool = pool
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.put(
            "/admin/octocat/hello-world/public-status", json={"enabled": True}
        )
    assert response.status_code == 401


@pytest.mark.asyncio
async def test_add_health_check_target(pool, monkeypatch):
    client = await _logged_in_client(pool, monkeypatch, installation_id=100)
    async with client:
        response = await client.post(
            "/admin/octocat/hello-world/health-targets",
            json={
                "label": "Production",
                "base_url": "https://api.example.com",
                "latency_threshold_ms": 3000,
            },
        )
    assert response.status_code == 200
    assert "id" in response.json()


@pytest.mark.asyncio
async def test_add_health_check_target_returns_422_for_non_integer_threshold(pool, monkeypatch):
    client = await _logged_in_client(pool, monkeypatch, installation_id=100)
    async with client:
        response = await client.post(
            "/admin/octocat/hello-world/health-targets",
            json={
                "label": "Production",
                "base_url": "https://api.example.com",
                "latency_threshold_ms": "not-a-number",
            },
        )
    assert response.status_code == 422


@pytest.mark.asyncio
async def test_add_health_check_target_enforces_plan_limit(pool, monkeypatch):
    # Pro's included limit is 5 (INCLUDED_HEALTH_CHECK_TARGETS) - filling
    # it up, then the 6th add should be rejected.
    client = await _logged_in_client(pool, monkeypatch, installation_id=100, plan="air")
    async with client:
        for i in range(5):
            response = await client.post(
                "/admin/octocat/hello-world/health-targets",
                json={"label": f"Target {i}", "base_url": f"https://api{i}.example.com", "latency_threshold_ms": None},
            )
            assert response.status_code == 200
        sixth = await client.post(
            "/admin/octocat/hello-world/health-targets",
            json={"label": "One too many", "base_url": "https://api6.example.com", "latency_threshold_ms": None},
        )
    assert sixth.status_code == 409
    assert "limit reached" in sixth.json()["detail"]


@pytest.mark.asyncio
async def test_add_health_check_target_rejects_internal_address(pool, monkeypatch):
    client = await _logged_in_client(pool, monkeypatch, installation_id=100)
    monkeypatch.setattr(
        "app_server.url_validation.socket.getaddrinfo",
        lambda *a, **k: [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("10.0.0.5", 443))],
    )
    async with client:
        response = await client.post(
            "/admin/octocat/hello-world/health-targets",
            json={"label": "Internal", "base_url": "https://internal-service.local", "latency_threshold_ms": 3000},
        )
    assert response.status_code == 400


@pytest.mark.asyncio
async def test_add_health_check_target_requires_login(pool):
    app.state.db_pool = pool
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.post(
            "/admin/octocat/hello-world/health-targets",
            json={"label": "Production", "base_url": "https://api.example.com", "latency_threshold_ms": None},
        )
    assert response.status_code == 401


@pytest.mark.asyncio
async def test_remove_health_check_target(pool, monkeypatch):
    client = await _logged_in_client(pool, monkeypatch, installation_id=100)
    async with client:
        add_response = await client.post(
            "/admin/octocat/hello-world/health-targets",
            json={"label": "Production", "base_url": "https://api.example.com", "latency_threshold_ms": None},
        )
        target_id = add_response.json()["id"]
        remove_response = await client.delete(f"/admin/octocat/hello-world/health-targets/{target_id}")
        page = await client.get("/admin/octocat/hello-world")
    assert remove_response.status_code == 200
    assert page.json()["health_targets"] == []


@pytest.mark.asyncio
async def test_set_webhook_url_rejects_internal_address(pool, monkeypatch):
    client = await _logged_in_client(pool, monkeypatch)
    monkeypatch.setattr(
        "app_server.url_validation.socket.getaddrinfo",
        lambda *a, **k: [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("169.254.169.254", 443))],
    )
    async with client:
        response = await client.put(
            "/admin/octocat/hello-world/webhook-url",
            json={"webhook_url": "https://metadata.internal/latest/meta-data"},
        )
    assert response.status_code == 400


@pytest.mark.asyncio
async def test_set_webhook_url_rejects_non_https(pool, monkeypatch):
    client = await _logged_in_client(pool, monkeypatch)
    async with client:
        response = await client.put(
            "/admin/octocat/hello-world/webhook-url",
            json={"webhook_url": "http://hooks.slack.com/services/x"},
        )
    assert response.status_code == 400




@pytest.mark.asyncio
async def test_my_installations_returns_only_paid_and_administered(pool, monkeypatch):
    await upsert_installation(pool, 100, "acme")
    await set_installation_plan(pool, 100, "indie")
    await upsert_installation(pool, 200, "free-org")
    await set_installation_plan(pool, 200, "free")
    await upsert_installation(pool, 300, "not-mine")
    await set_installation_plan(pool, 300, "indie")
    await _mock_github_installations(monkeypatch, [100, 200])

    app.state.db_pool = pool
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.get(
            "/v1/my-installations",
            headers={"Authorization": "Bearer gho_faketoken"},
        )

    assert response.status_code == 200
    installations = response.json()["installations"]
    assert [installation["installation_id"] for installation in installations] == [100]
    assert installations[0]["account_login"] == "acme"


@pytest.mark.asyncio
async def test_my_installations_requires_bearer_token(pool):
    app.state.db_pool = pool
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.get("/v1/my-installations")
    assert response.status_code == 401


@pytest.mark.asyncio
async def test_create_cli_token_mints_token_for_administered_paid_installation(pool, monkeypatch):
    await upsert_installation(pool, 100, "acme")
    await set_installation_plan(pool, 100, "indie")
    await _mock_github_installations(monkeypatch, [100])

    app.state.db_pool = pool
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.post(
            "/v1/cli-tokens",
            json={"installation_id": 100, "label": "laptop (device flow)"},
            headers={"Authorization": "Bearer gho_faketoken"},
        )

    assert response.status_code == 200
    assert len(response.json()["token"]) > 20


@pytest.mark.asyncio
async def test_create_cli_token_rejects_unadministered_installation(pool, monkeypatch):
    await upsert_installation(pool, 100, "acme")
    await set_installation_plan(pool, 100, "indie")
    await _mock_github_installations(monkeypatch, [999])

    app.state.db_pool = pool
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.post(
            "/v1/cli-tokens",
            json={"installation_id": 100, "label": "x"},
            headers={"Authorization": "Bearer gho_faketoken"},
        )

    assert response.status_code == 403


@pytest.mark.asyncio
async def test_create_cli_token_rejects_free_plan(pool, monkeypatch):
    await upsert_installation(pool, 100, "acme")
    await set_installation_plan(pool, 100, "free")
    await _mock_github_installations(monkeypatch, [100])

    app.state.db_pool = pool
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.post(
            "/v1/cli-tokens",
            json={"installation_id": 100, "label": "x"},
            headers={"Authorization": "Bearer gho_faketoken"},
        )

    assert response.status_code == 402


@pytest.mark.asyncio
async def test_create_cli_token_enforces_seat_cap(pool, monkeypatch):
    await upsert_installation(pool, 100, "acme")
    await set_installation_plan(pool, 100, "indie")
    await _mock_github_installations(monkeypatch, [100])

    app.state.db_pool = pool
    transport = ASGITransport(app=app)
    max_tokens = await get_max_tokens(pool, 100)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        for i in range(max_tokens):
            response = await client.post(
                "/v1/cli-tokens",
                json={"installation_id": 100, "label": f"token-{i}"},
                headers={"Authorization": "Bearer gho_faketoken"},
            )
            assert response.status_code == 200
        over_limit = await client.post(
            "/v1/cli-tokens",
            json={"installation_id": 100, "label": "one-too-many"},
            headers={"Authorization": "Bearer gho_faketoken"},
        )

    assert over_limit.status_code == 409


@pytest.mark.asyncio
async def test_create_cli_token_returns_422_for_missing_fields(pool, monkeypatch):
    await upsert_installation(pool, 100, "acme")
    await set_installation_plan(pool, 100, "indie")
    await _mock_github_installations(monkeypatch, [100])

    app.state.db_pool = pool
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.post(
            "/v1/cli-tokens",
            json={"installation_id": 100},
            headers={"Authorization": "Bearer gho_faketoken"},
        )

    assert response.status_code == 422


@pytest.mark.asyncio
async def test_create_cli_token_rejects_invalid_label(pool, monkeypatch):
    await upsert_installation(pool, 100, "acme")
    await set_installation_plan(pool, 100, "indie")
    await _mock_github_installations(monkeypatch, [100])

    app.state.db_pool = pool
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.post(
            "/v1/cli-tokens",
            json={"installation_id": 100, "label": "bad\nlabel"},
            headers={"Authorization": "Bearer gho_faketoken"},
        )

    assert response.status_code == 422


@pytest.mark.asyncio
async def test_create_cli_token_requires_bearer_token(pool):
    app.state.db_pool = pool
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.post("/v1/cli-tokens", json={"installation_id": 100, "label": "x"})
    assert response.status_code == 401


async def _second_session_client(monkeypatch, github_user_id: int, login: str, session_id: str):
    from app_server.db import create_session

    pool = app.state.db_pool
    await create_session(
        pool,
        session_id,
        github_user_id,
        login,
        encrypt_access_token("gho_faketoken2", "test-session-secret"),
        datetime.now(timezone.utc) + timedelta(hours=1),
    )
    signed = sign_session_id(session_id, "test-session-secret")
    transport = ASGITransport(app=app)
    return AsyncClient(transport=transport, base_url="http://test", cookies={"session": signed})


@pytest.mark.asyncio
async def test_first_admin_to_arrive_is_auto_seated(pool, monkeypatch):
    client = await _logged_in_client(pool, monkeypatch)
    async with client:
        response = await client.get("/admin/octocat/hello-world")
    assert response.status_code == 200
    assert [m["github_login"] for m in response.json()["members"]] == ["octocat"]
    assert response.json()["seat_limit"] == 3


@pytest.mark.asyncio
async def test_second_github_admin_without_a_seat_is_rejected(pool, monkeypatch):
    first = await _logged_in_client(pool, monkeypatch)
    async with first:
        await first.get("/admin/octocat/hello-world")  # bootstraps octocat as seat one

    second = await _second_session_client(monkeypatch, 43, "alice", "sess-2")
    async with second:
        response = await second.get("/admin/octocat/hello-world")
    assert response.status_code == 403


@pytest.mark.asyncio
async def test_adding_a_member_grants_them_access(pool, monkeypatch):
    first = await _logged_in_client(pool, monkeypatch)
    async with first:
        await first.get("/admin/octocat/hello-world")
        add_response = await first.post("/admin/octocat/hello-world/members", json={"github_login": "alice"})
    assert add_response.status_code == 200

    second = await _second_session_client(monkeypatch, 43, "alice", "sess-2")
    async with second:
        response = await second.get("/admin/octocat/hello-world")
    assert response.status_code == 200


@pytest.mark.asyncio
async def test_removing_a_member_revokes_access(pool, monkeypatch):
    first = await _logged_in_client(pool, monkeypatch)
    async with first:
        await first.get("/admin/octocat/hello-world")
        await first.post("/admin/octocat/hello-world/members", json={"github_login": "alice"})
        remove_response = await first.delete("/admin/octocat/hello-world/members/alice")
    assert remove_response.status_code == 200

    second = await _second_session_client(monkeypatch, 43, "alice", "sess-2")
    async with second:
        response = await second.get("/admin/octocat/hello-world")
    assert response.status_code == 403


@pytest.mark.asyncio
async def test_add_member_enforces_seat_cap(pool, monkeypatch):
    client = await _logged_in_client(pool, monkeypatch)
    async with client:
        await client.get("/admin/octocat/hello-world")  # seats octocat (1 of 5)
        await client.post("/admin/octocat/hello-world/members", json={"github_login": "alice"})  # 2 of 5
        await client.post("/admin/octocat/hello-world/members", json={"github_login": "bob"})  # 3 of 5
        await client.post("/admin/octocat/hello-world/members", json={"github_login": "carol"})  # 4 of 5
        await client.post("/admin/octocat/hello-world/members", json={"github_login": "dave"})  # 5 of 5
        response = await client.post("/admin/octocat/hello-world/members", json={"github_login": "erin"})
    assert response.status_code == 409
    assert "seat limit reached" in response.json()["detail"]


def test_build_updated_seat_items_adds_new_seat_item_when_none_exists():
    items = _build_updated_seat_items(
        [{"price": {"id": "pri_base"}, "quantity": 1}], delta=1
    )
    assert {"price_id": "pri_base", "quantity": 1} in items
    assert {"price_id": EXTRA_SEAT_PRICE_ID, "quantity": 1} in items


def test_build_updated_seat_items_increments_existing_seat_item():
    items = _build_updated_seat_items(
        [
            {"price": {"id": "pri_base"}, "quantity": 1},
            {"price": {"id": EXTRA_SEAT_PRICE_ID}, "quantity": 2},
        ],
        delta=1,
    )
    assert {"price_id": EXTRA_SEAT_PRICE_ID, "quantity": 3} in items


def test_build_updated_seat_items_decrements_and_drops_item_at_zero():
    items = _build_updated_seat_items(
        [
            {"price": {"id": "pri_base"}, "quantity": 1},
            {"price": {"id": EXTRA_SEAT_PRICE_ID}, "quantity": 1},
        ],
        delta=-1,
    )
    assert items == [{"price_id": "pri_base", "quantity": 1}]


def test_build_updated_seat_items_returns_none_when_nothing_to_remove():
    assert _build_updated_seat_items([{"price": {"id": "pri_base"}, "quantity": 1}], delta=-1) is None


@pytest.mark.asyncio
async def test_buy_extra_seat_requires_active_subscription(pool, monkeypatch):
    client = await _logged_in_client(pool, monkeypatch)
    async with client:
        response = await client.post("/admin/octocat/hello-world/seats/buy")
    assert response.status_code == 400


@pytest.mark.asyncio
async def test_buy_extra_seat_degrades_gracefully_without_api_key_configured(pool, monkeypatch):
    # Real-world current state until PADDLE_API_KEY is added to the server's
    # env - must fail as a clean 502, not crash the request with a 500.
    await upsert_installation(pool, 100, "octocat")
    await add_paddle_ids_to_installation(pool, 100, "sub_test_seat", "ctm_test_seat")
    client = await _logged_in_client(pool, monkeypatch)

    async with client:
        response = await client.post("/admin/octocat/hello-world/seats/buy")

    assert response.status_code == 502


@pytest.mark.asyncio
async def test_buy_extra_seat_updates_paddle_subscription(pool, monkeypatch):
    await upsert_installation(pool, 100, "octocat")
    await add_paddle_ids_to_installation(pool, 100, "sub_test_seat", "ctm_test_seat")
    client = await _logged_in_client(pool, monkeypatch)

    monkeypatch.setattr(
        "app_server.admin.get_paddle_subscription",
        lambda api_key, sub_id: {"items": [{"price": {"id": "pri_base"}, "quantity": 1}]},
    )
    captured = {}

    def fake_update(api_key, sub_id, items, proration_billing_mode):
        captured["sub_id"] = sub_id
        captured["items"] = items
        captured["mode"] = proration_billing_mode
        return {}

    monkeypatch.setattr("app_server.admin.update_paddle_subscription_items", fake_update)

    async with client:
        response = await client.post("/admin/octocat/hello-world/seats/buy")

    assert response.status_code == 200
    assert captured["sub_id"] == "sub_test_seat"
    assert {"price_id": EXTRA_SEAT_PRICE_ID, "quantity": 1} in captured["items"]
    assert captured["mode"] == "prorated_immediately"


@pytest.mark.asyncio
async def test_buy_extra_seat_reports_paddle_api_failure(pool, monkeypatch):
    await upsert_installation(pool, 100, "octocat")
    await add_paddle_ids_to_installation(pool, 100, "sub_test_seat", "ctm_test_seat")
    client = await _logged_in_client(pool, monkeypatch)

    def _boom(api_key, sub_id):
        raise PaddleAPIError("could not fetch subscription")

    monkeypatch.setattr("app_server.admin.get_paddle_subscription", _boom)

    async with client:
        response = await client.post("/admin/octocat/hello-world/seats/buy")

    assert response.status_code == 502


def test_concurrent_buy_extra_seat_calls_do_not_lose_an_update():
    # _adjust_extra_seat_sync does a read-then-write against Paddle's
    # subscription: get_paddle_subscription, then a delta computed off
    # whatever it returned, then update_subscription_items. Two concurrent
    # calls for the same installation can both read the same starting
    # quantity before either write lands, so the second write silently
    # clobbers the first instead of stacking - two "buy" clicks net only +1
    # seat, both requests still reporting success.
    #
    # This calls the real buy_extra_seat route function directly (auth,
    # session and DB-logging dependencies mocked out) rather than
    # reimplementing its body, so it actually verifies the endpoint wires
    # _seat_adjustment_lock around the critical section - not just that the
    # lock helper works in isolation. Asserts peak concurrent entry into the
    # fake Paddle rather than only the final quantity, since that's a
    # direct, deterministic measurement of serialization rather than
    # something that depends on exact read/write interleaving to go wrong
    # in a specific way. Confirmed this reproduces the bug: temporarily
    # removing the "async with _seat_adjustment_lock(...)" wrapper from
    # buy_extra_seat makes this fail with peak_concurrency == 2.
    fake_paddle_state = {"quantity": 0}
    concurrency = {"current": 0, "peak": 0}
    concurrency_lock = threading.Lock()

    def fake_get(api_key, sub_id):
        with concurrency_lock:
            concurrency["current"] += 1
            concurrency["peak"] = max(concurrency["peak"], concurrency["current"])
        time.sleep(0.3)
        v = fake_paddle_state["quantity"]
        with concurrency_lock:
            concurrency["current"] -= 1
        return {"items": [{"price": {"id": EXTRA_SEAT_PRICE_ID}, "quantity": v}]}

    def fake_update(api_key, sub_id, items, proration_billing_mode):
        fake_paddle_state["quantity"] = items[0]["quantity"]
        return {}

    fake_installation = {"installation_id": 100, "paddle_subscription_id": "sub_test_seat"}

    async def fake_require_admin_installation(request, org, repo):
        return fake_installation

    async def fake_get_current_session(request):
        return {"github_login": "octocat"}

    async def fake_record_admin_action(*args, **kwargs):
        return None

    with patch("app_server.admin.get_paddle_subscription", fake_get), \
         patch("app_server.admin.update_paddle_subscription_items", fake_update), \
         patch("app_server.admin._require_admin_installation", fake_require_admin_installation), \
         patch("app_server.admin.get_current_session", fake_get_current_session), \
         patch("app_server.admin.record_admin_action", fake_record_admin_action), \
         patch("app_server.admin.get_settings", lambda: MagicMock(paddle_api_key="fake-api-key")):

        async def main():
            fake_request = MagicMock()
            responses = await asyncio.gather(
                admin.buy_extra_seat("octocat", "hello-world", fake_request),
                admin.buy_extra_seat("octocat", "hello-world", fake_request),
            )
            return responses

        responses = asyncio.run(main())

    assert responses == [{"ok": True}, {"ok": True}]
    assert concurrency["peak"] == 1
    assert fake_paddle_state["quantity"] == 2


@pytest.mark.asyncio
async def test_remove_extra_seat_requires_active_subscription(pool, monkeypatch):
    client = await _logged_in_client(pool, monkeypatch)
    async with client:
        response = await client.post("/admin/octocat/hello-world/seats/remove")
    assert response.status_code == 400


@pytest.mark.asyncio
async def test_remove_extra_seat_returns_409_when_no_seats_to_remove(pool, monkeypatch):
    await upsert_installation(pool, 100, "octocat")
    await add_paddle_ids_to_installation(pool, 100, "sub_test_seat", "ctm_test_seat")
    client = await _logged_in_client(pool, monkeypatch)

    monkeypatch.setattr(
        "app_server.admin.get_paddle_subscription",
        lambda api_key, sub_id: {"items": [{"price": {"id": "pri_base"}, "quantity": 1}]},
    )
    called = []
    monkeypatch.setattr(
        "app_server.admin.update_paddle_subscription_items",
        lambda *a, **k: called.append(True),
    )

    async with client:
        response = await client.post("/admin/octocat/hello-world/seats/remove")

    assert response.status_code == 409
    assert called == []


@pytest.mark.asyncio
async def test_remove_extra_seat_updates_paddle_subscription(pool, monkeypatch):
    await upsert_installation(pool, 100, "octocat")
    await add_paddle_ids_to_installation(pool, 100, "sub_test_seat", "ctm_test_seat")
    client = await _logged_in_client(pool, monkeypatch)

    monkeypatch.setattr(
        "app_server.admin.get_paddle_subscription",
        lambda api_key, sub_id: {
            "items": [
                {"price": {"id": "pri_base"}, "quantity": 1},
                {"price": {"id": EXTRA_SEAT_PRICE_ID}, "quantity": 2},
            ]
        },
    )
    captured = {}
    monkeypatch.setattr(
        "app_server.admin.update_paddle_subscription_items",
        lambda api_key, sub_id, items, proration_billing_mode: captured.update(items=items),
    )

    async with client:
        response = await client.post("/admin/octocat/hello-world/seats/remove")

    assert response.status_code == 200
    assert {"price_id": EXTRA_SEAT_PRICE_ID, "quantity": 1} in captured["items"]


@pytest.mark.asyncio
async def test_billing_portal_requires_billing_account_on_file(pool, monkeypatch):
    client = await _logged_in_client(pool, monkeypatch)
    monkeypatch.setattr("app_server.admin._has_real_admin_permission", _async_true)
    async with client:
        response = await client.get("/admin/octocat/hello-world/billing-portal")
    assert response.status_code == 400


@pytest.mark.asyncio
async def test_billing_portal_accessible_on_free_plan(pool, monkeypatch):
    # A payment failure has already downgraded the installation to free by
    # the time anyone would use this - it must NOT be behind the same
    # plan=='free' -> 402 gate every other paid-plan feature uses, or the
    # one person who needs to fix their card gets locked out of doing so.
    await upsert_installation(pool, 100, "octocat")
    await set_installation_plan(pool, 100, "free")
    await add_paddle_ids_to_installation(pool, 100, "sub_test", "ctm_test")
    client = await _logged_in_client(pool, monkeypatch, plan="free")

    monkeypatch.setattr(
        "app_server.admin.create_portal_session",
        lambda api_key, customer_id, subscription_ids: {
            "urls": {"general": {"overview": "https://customer-portal.paddle.com/overview"}}
        },
    )
    monkeypatch.setattr("app_server.admin._has_real_admin_permission", _async_true)

    async with client:
        response = await client.get("/admin/octocat/hello-world/billing-portal")

    assert response.status_code == 200


@pytest.mark.asyncio
async def test_billing_portal_returns_subscription_scoped_url(pool, monkeypatch):
    # Response shape confirmed against a real Paddle portal session:
    # each subscriptions[] entry has update_subscription_payment_method
    # directly on it, not nested under a further "urls" key.
    await upsert_installation(pool, 100, "octocat")
    await add_paddle_ids_to_installation(pool, 100, "sub_test", "ctm_test")
    client = await _logged_in_client(pool, monkeypatch)

    captured = {}

    def fake_create_portal_session(api_key, customer_id, subscription_ids):
        captured["customer_id"] = customer_id
        captured["subscription_ids"] = subscription_ids
        return {
            "urls": {
                "general": {"overview": "https://customer-portal.paddle.com/overview"},
                "subscriptions": [
                    {
                        "id": "sub_test",
                        "cancel_subscription": "https://customer-portal.paddle.com/cancel",
                        "update_subscription_payment_method": "https://customer-portal.paddle.com/update-payment",
                    }
                ],
            }
        }

    monkeypatch.setattr("app_server.admin.create_portal_session", fake_create_portal_session)
    monkeypatch.setattr("app_server.admin._has_real_admin_permission", _async_true)

    async with client:
        response = await client.get("/admin/octocat/hello-world/billing-portal")

    assert response.status_code == 200
    assert response.json() == {"url": "https://customer-portal.paddle.com/update-payment"}
    assert captured["customer_id"] == "ctm_test"
    assert captured["subscription_ids"] == ["sub_test"]


@pytest.mark.asyncio
async def test_billing_portal_falls_back_to_general_url_without_subscription(pool, monkeypatch):
    await upsert_installation(pool, 100, "octocat")
    await add_paddle_ids_to_installation(pool, 100, None, "ctm_test")
    client = await _logged_in_client(pool, monkeypatch)

    monkeypatch.setattr(
        "app_server.admin.create_portal_session",
        lambda api_key, customer_id, subscription_ids: {
            "urls": {"general": {"overview": "https://customer-portal.paddle.com/overview"}}
        },
    )
    monkeypatch.setattr("app_server.admin._has_real_admin_permission", _async_true)

    async with client:
        response = await client.get("/admin/octocat/hello-world/billing-portal")

    assert response.status_code == 200
    assert response.json() == {"url": "https://customer-portal.paddle.com/overview"}


@pytest.mark.asyncio
async def test_billing_portal_reports_paddle_api_failure(pool, monkeypatch):
    await upsert_installation(pool, 100, "octocat")
    await add_paddle_ids_to_installation(pool, 100, "sub_test", "ctm_test")
    client = await _logged_in_client(pool, monkeypatch)

    def _boom(api_key, customer_id, subscription_ids):
        raise PaddleAPIError("could not create portal session")

    monkeypatch.setattr("app_server.admin.create_portal_session", _boom)
    monkeypatch.setattr("app_server.admin._has_real_admin_permission", _async_true)

    async with client:
        response = await client.get("/admin/octocat/hello-world/billing-portal")

    assert response.status_code == 502


@pytest.mark.asyncio
async def test_billing_portal_denies_a_non_admin_in_the_coarse_installations_set(pool, monkeypatch):
    # The gap this closes: GET /user/installations (the coarse set
    # _require_authorized_installation checks) includes anyone with mere
    # read access to one repo the app covers, per GitHub's own docs - not
    # enough to trust with a session that can view/change a payment method
    # or cancel the subscription. Real per-repo permission ("admin") must
    # be verified before an unseated caller reaches this.
    await upsert_installation(pool, 100, "octocat")
    await add_paddle_ids_to_installation(pool, 100, "sub_test", "ctm_test")
    client = await _logged_in_client(pool, monkeypatch)
    monkeypatch.setattr("app_server.admin._has_real_admin_permission", _async_false)

    async with client:
        response = await client.get("/admin/octocat/hello-world/billing-portal")

    assert response.status_code == 403


@pytest.mark.asyncio
async def test_seat_claim_denied_for_a_non_admin_in_the_coarse_installations_set(pool, monkeypatch):
    # Same gap as above, for the other place the coarse set was trusted
    # alone: claiming the first seat on a paid installation with none yet -
    # which would otherwise hand the claimant /admin/{org}/{repo} access
    # (API tokens, team management), not just the billing portal.
    client = await _logged_in_client(pool, monkeypatch, plan="air")
    monkeypatch.setattr("app_server.admin._has_real_admin_permission", _async_false)

    async with client:
        response = await client.get("/admin/octocat/hello-world")

    assert response.status_code == 403
    assert "admin access" in response.json()["detail"]
    assert await is_installation_member(pool, 100, "octocat") is False


@pytest.mark.asyncio
async def test_has_real_admin_permission_true_only_for_admin_level(monkeypatch):
    monkeypatch.setattr("app_server.admin.generate_app_jwt", lambda *a, **k: "fake-jwt")
    monkeypatch.setattr("app_server.admin.get_installation_token", lambda *a, **k: "fake-installation-token")
    monkeypatch.setattr("app_server.admin.get_repo_permission_for_user", lambda *a, **k: "admin")

    assert await _has_real_admin_permission(100, "octocat", "octocat/hello-world") is True


@pytest.mark.asyncio
async def test_has_real_admin_permission_false_for_read_or_write(monkeypatch):
    monkeypatch.setattr("app_server.admin.generate_app_jwt", lambda *a, **k: "fake-jwt")
    monkeypatch.setattr("app_server.admin.get_installation_token", lambda *a, **k: "fake-installation-token")
    for permission in ("read", "write", "none"):
        monkeypatch.setattr(
            "app_server.admin.get_repo_permission_for_user", lambda *a, permission=permission, **k: permission
        )
        assert await _has_real_admin_permission(100, "octocat", "octocat/hello-world") is False


@pytest.mark.asyncio
async def test_has_real_admin_permission_fails_closed_on_a_github_error(monkeypatch):
    monkeypatch.setattr("app_server.admin.generate_app_jwt", lambda *a, **k: "fake-jwt")

    def _boom(*a, **k):
        raise RuntimeError("GitHub API unavailable")

    monkeypatch.setattr("app_server.admin.get_installation_token", _boom)

    assert await _has_real_admin_permission(100, "octocat", "octocat/hello-world") is False


@pytest.mark.asyncio
async def test_seat_claim_still_succeeds_for_a_verified_real_admin(pool, monkeypatch):
    # The legitimate path this must not break: a genuinely-admin caller
    # with no seat yet (a paid installation from before seats existed, or
    # the purchase webhook hasn't landed) still becomes seat one.
    client = await _logged_in_client(pool, monkeypatch, plan="air")

    async with client:
        response = await client.get("/admin/octocat/hello-world")

    assert response.status_code == 200
    assert await is_installation_member(pool, 100, "octocat") is True


@pytest.mark.asyncio
async def test_add_member_rejects_invalid_github_login(pool, monkeypatch):
    client = await _logged_in_client(pool, monkeypatch)
    async with client:
        response = await client.post("/admin/octocat/hello-world/members", json={"github_login": "-bad-login-"})
    assert response.status_code == 422


@pytest.mark.asyncio
async def test_repo_installation_id_resolves_without_any_scan_history(pool):
    # The exact gap this closes: a repo connected to a paid installation
    # but never scanned yet used to 404 here (repo_history was the only
    # lookup), which meant no route built on this could ever reach its
    # own "nothing scanned yet" handling - see get_dashboard_docs.
    await upsert_installation(pool, 900, "octocat")

    installation_id = await _repo_installation_id(pool, "octocat", "never-scanned-repo")

    assert installation_id == 900


@pytest.mark.asyncio
async def test_repo_installation_id_falls_back_to_repo_history(pool):
    # account_login drift (e.g. a GitHub account rename after this
    # installation row was written) shouldn't break resolution for a repo
    # that already has real scan history recorded under the old name.
    await upsert_installation(pool, 901, "renamed-account")
    await insert_repo_history(
        pool, 901, "old-account-name/some-repo", datetime.now(timezone.utc), {"scanned_at": "x"}
    )

    installation_id = await _repo_installation_id(pool, "old-account-name", "some-repo")

    assert installation_id == 901


@pytest.mark.asyncio
async def test_repo_installation_id_raises_404_when_truly_unresolvable(pool):
    with pytest.raises(HTTPException) as exc_info:
        await _repo_installation_id(pool, "no-such-account", "no-such-repo")

    assert exc_info.value.status_code == 404
