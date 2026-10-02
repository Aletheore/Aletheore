from datetime import datetime, timedelta, timezone

import httpx
import pytest
from httpx import ASGITransport, AsyncClient

from aletheore.evidence import EVIDENCE_VERSION
from app_server import frontend
from app_server.auth import encrypt_access_token, sign_session_id
from app_server.db import create_session, insert_repo_history, set_installation_plan, upsert_installation
from app_server.email_templates import credit_exhausted_email, credit_low_balance_email
from app_server.main import app

AIR_NAV_LABELS = ["Overview", "Findings", "Dead code", "Endpoint health", "AIRview", "Docs", "Settings"]


async def _async_true(*args, **kwargs):
    return True


async def _client(pool, monkeypatch, administered_ids):
    monkeypatch.setenv("SESSION_SECRET", "test-session-secret")
    await create_session(
        pool, "sess-1", 42, "octocat",
        encrypt_access_token("gho_faketoken", "test-session-secret"),
        datetime.now(timezone.utc) + timedelta(hours=1),
    )

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={"total_count": len(administered_ids), "installations": [{"id": i} for i in administered_ids]},
        )

    monkeypatch.setattr(
        "app_server.admin._github_http_client",
        lambda: httpx.Client(transport=httpx.MockTransport(handler), base_url="https://api.github.com"),
    )
    monkeypatch.setattr("app_server.admin._has_real_admin_permission", _async_true)
    app.state.db_pool = pool
    signed = sign_session_id("sess-1", "test-session-secret")
    return AsyncClient(transport=ASGITransport(app=app), base_url="http://test", cookies={"session": signed})


async def _installation(pool, installation_id, login, plan, repo=None):
    await upsert_installation(pool, installation_id, login)
    await set_installation_plan(pool, installation_id, plan)
    if repo:
        await insert_repo_history(
            pool, installation_id, f"{login}/{repo}", datetime.now(timezone.utc),
            {"aletheore_version": EVIDENCE_VERSION, "repository": {"modules": []}},
        )


# --- the AIR page -----------------------------------------------------------

@pytest.mark.asyncio
async def test_air_usage_page_is_served_inside_the_air_shell(pool, monkeypatch):
    client = await _client(pool, monkeypatch, administered_ids=[])
    async with client:
        response = await client.get("/dashboard/acme/service/usage")

    assert response.status_code == 200
    html = response.text
    for label in AIR_NAV_LABELS:
        assert label in html, f"AIR navigation lost {label}"
    assert "Usage &amp; credit" in html
    assert "Your installs" not in html
    assert 'id="credits-root"' in html
    assert "no-store" in response.headers.get("cache-control", "")


@pytest.mark.asyncio
async def test_usage_page_requires_login(pool):
    app.state.db_pool = pool
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.get("/dashboard/acme/service/usage", follow_redirects=False)

    assert response.status_code == 307
    assert response.headers["location"] == "/"


def test_every_air_page_links_to_usage_and_credit_and_marks_it_active_on_its_own_page():
    assert 'data-href="/usage"' in frontend._sidebar("overview")
    assert 'nav-item active" data-href="/usage"' in frontend._sidebar("usage")
    assert 'nav-item active" data-href="/usage"' not in frontend._sidebar("overview")


def test_the_air_usage_page_has_no_upgrade_cross_sell():
    assert 'id="upgrade-card"' not in frontend._usage_html()


def test_the_air_usage_page_finds_its_installation_from_the_repo():
    html = frontend._usage_html()

    assert "useInstallation(" in html
    assert "adminBase" in html


# --- the Flash page keeps its own shell -------------------------------------

def test_the_flash_credits_page_keeps_the_flash_shell_without_air_navigation():
    html = frontend._credits_page(555)

    assert "Your installs" in html
    # The AIR navigation links (not the upgrade card's mention of AIRview).
    assert 'data-href="/wiki"' not in html and 'data-href="/docs"' not in html
    assert 'id="upgrade-card"' in html
    assert "let installationId = 555;" in html


# --- one credit implementation ----------------------------------------------

def test_settings_no_longer_sells_credit_and_points_to_the_usage_page():
    html = frontend._settings_html()

    assert "buyCredit" not in html
    assert "topup-amount" not in html
    assert "cdn.paddle.com" not in html
    assert 'href="\' + pageBase + \'/usage"' in html or "/usage" in html


def test_buy_credit_is_defined_in_exactly_one_place():
    assert "async function buyCredit(" not in frontend.BILLING_ACTIONS_JS
    assert frontend._CREDITS_JS.count("async function buyCredit(") == 1


# --- old links and emails keep working --------------------------------------

@pytest.mark.asyncio
async def test_an_air_installation_credits_link_redirects_to_its_usage_page(pool, monkeypatch):
    await _installation(pool, 801, "acme", "air", repo="service")
    client = await _client(pool, monkeypatch, administered_ids=[801])
    async with client:
        response = await client.get("/credits/801", follow_redirects=False)

    assert response.status_code == 307
    assert response.headers["location"] == "/dashboard/acme/service/usage"


@pytest.mark.asyncio
async def test_a_flash_installation_credits_link_is_not_redirected(pool, monkeypatch):
    await _installation(pool, 802, "acme", "flash", repo="service")
    client = await _client(pool, monkeypatch, administered_ids=[802])
    async with client:
        response = await client.get("/credits/802", follow_redirects=False)

    assert response.status_code == 200
    assert "Your installs" in response.text


@pytest.mark.asyncio
async def test_an_air_installation_without_a_repo_still_gets_the_credits_page(pool, monkeypatch):
    await _installation(pool, 803, "acme", "air")
    client = await _client(pool, monkeypatch, administered_ids=[803])
    async with client:
        response = await client.get("/credits/803", follow_redirects=False)

    assert response.status_code == 200


@pytest.mark.asyncio
async def test_an_installation_the_caller_does_not_administer_is_never_redirected_or_named(pool, monkeypatch):
    await _installation(pool, 804, "secret-org", "air", repo="private-repo")
    client = await _client(pool, monkeypatch, administered_ids=[999])
    async with client:
        response = await client.get("/credits/804", follow_redirects=False)

    assert response.status_code == 200
    assert "secret-org" not in response.text and "private-repo" not in response.text


def test_credit_emails_link_an_air_customer_to_the_credits_address_too():
    for build in (credit_low_balance_email, credit_exhausted_email):
        message = build(
            account_login="acme", plan="air",
            base_credit_remaining_usd=0.50, topup_credit_balance_usd=0.00,
            installation_id=4242,
        )
        assert "https://app.aletheore.com/credits/4242" in message["html"]


# --- Flash goes straight to its credit page on login -------------------------

def test_a_login_with_only_one_flash_installation_lands_straight_on_its_credit_page():
    html = frontend._picker_html()

    # Flash has no dashboard, so the picker would be a one-card page to click
    # through. It only redirects when there is nothing else to choose from.
    assert "data.repos.length === 0 && billingAccounts.length === 1" in html
    assert "window.location.replace('/credits/'" in html


def test_the_usage_page_explains_every_way_the_installation_lookup_can_fail():
    # apiGet returns null for ANY non-OK response, so it cannot tell a free
    # plan from a server error; the page asks the admin API directly and says
    # which one happened instead of staying blank.
    html = frontend._usage_html()
    start = html.index("async function initCredits()")
    init = html[start:html.index("initCredits();", start)]

    assert "apiGet(" not in init
    assert "res.status === 402" in init
    assert "could not load Usage &amp; credit right now" in init
    assert "catch (e)" in init


def test_a_load_error_hides_the_empty_placeholders_under_the_message():
    html = frontend._usage_html()

    assert 'id="review-head"' in html
    assert "['review-head', 'review-history-body', 'flash-settings-grid']" in html
