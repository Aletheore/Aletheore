from datetime import datetime, timezone
from decimal import Decimal

import pytest
from httpx import ASGITransport, AsyncClient

from app_server.affiliates import create_affiliate, record_commission, record_referral
from app_server.db import upsert_installation
from app_server.main import app
from app_server.paddle_client import PaddleAPIError

ADMIN_TOKEN = "test-affiliate-admin-token"


def _mock_create_discount(monkeypatch, discount_id: str = "dsc_mocked"):
    monkeypatch.setattr(
        "app_server.admin.create_paddle_discount",
        lambda api_key, code, description: {"id": discount_id, "code": code},
    )


@pytest.mark.asyncio
async def test_create_affiliate_returns_404_when_token_not_configured(pool, monkeypatch):
    monkeypatch.delenv("AFFILIATE_ADMIN_TOKEN", raising=False)
    app.state.db_pool = pool
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.post("/admin/affiliates", json={"code": "SARAH10", "name": "Sarah"})
    assert response.status_code == 404


@pytest.mark.asyncio
async def test_create_affiliate_requires_bearer_token(pool, monkeypatch):
    monkeypatch.setenv("AFFILIATE_ADMIN_TOKEN", ADMIN_TOKEN)
    app.state.db_pool = pool
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.post("/admin/affiliates", json={"code": "SARAH10", "name": "Sarah"})
    assert response.status_code == 401


@pytest.mark.asyncio
async def test_create_affiliate_rejects_wrong_token(pool, monkeypatch):
    monkeypatch.setenv("AFFILIATE_ADMIN_TOKEN", ADMIN_TOKEN)
    app.state.db_pool = pool
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.post(
            "/admin/affiliates",
            json={"code": "SARAH10", "name": "Sarah"},
            headers={"Authorization": "Bearer wrong-token"},
        )
    assert response.status_code == 401


@pytest.mark.asyncio
async def test_create_affiliate_creates_paddle_discount_and_db_row(pool, monkeypatch):
    monkeypatch.setenv("AFFILIATE_ADMIN_TOKEN", ADMIN_TOKEN)
    app.state.db_pool = pool
    _mock_create_discount(monkeypatch, discount_id="dsc_sarah_admin")

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.post(
            "/admin/affiliates",
            json={"code": "SARAH10", "name": "Sarah"},
            headers={"Authorization": f"Bearer {ADMIN_TOKEN}"},
        )

    assert response.status_code == 200
    body = response.json()
    assert body["code"] == "SARAH10"
    assert body["paddle_discount_id"] == "dsc_sarah_admin"
    assert body["name"] == "Sarah"


@pytest.mark.asyncio
async def test_create_affiliate_rejects_duplicate_code(pool, monkeypatch):
    monkeypatch.setenv("AFFILIATE_ADMIN_TOKEN", ADMIN_TOKEN)
    app.state.db_pool = pool
    await create_affiliate(pool, "MAYA10", "dsc_maya_existing", "Maya")
    _mock_create_discount(monkeypatch, discount_id="dsc_maya_new")

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.post(
            "/admin/affiliates",
            json={"code": "MAYA10", "name": "Maya Again"},
            headers={"Authorization": f"Bearer {ADMIN_TOKEN}"},
        )

    assert response.status_code == 409


@pytest.mark.asyncio
async def test_create_affiliate_surfaces_paddle_api_error_as_502(pool, monkeypatch):
    monkeypatch.setenv("AFFILIATE_ADMIN_TOKEN", ADMIN_TOKEN)
    app.state.db_pool = pool

    def _raise(api_key, code, description):
        raise PaddleAPIError("could not create discount")

    monkeypatch.setattr("app_server.admin.create_paddle_discount", _raise)

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.post(
            "/admin/affiliates",
            json={"code": "TOM10", "name": "Tom"},
            headers={"Authorization": f"Bearer {ADMIN_TOKEN}"},
        )

    assert response.status_code == 502


@pytest.mark.asyncio
async def test_create_affiliate_rejects_invalid_code(pool, monkeypatch):
    monkeypatch.setenv("AFFILIATE_ADMIN_TOKEN", ADMIN_TOKEN)
    app.state.db_pool = pool
    _mock_create_discount(monkeypatch)

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.post(
            "/admin/affiliates",
            json={"code": "not valid!", "name": "Nina"},
            headers={"Authorization": f"Bearer {ADMIN_TOKEN}"},
        )

    assert response.status_code == 422


@pytest.mark.asyncio
async def test_list_affiliates_requires_bearer_token(pool, monkeypatch):
    monkeypatch.setenv("AFFILIATE_ADMIN_TOKEN", ADMIN_TOKEN)
    app.state.db_pool = pool
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.get("/admin/affiliates")
    assert response.status_code == 401


@pytest.mark.asyncio
async def test_list_affiliates_returns_report_data(pool, monkeypatch):
    monkeypatch.setenv("AFFILIATE_ADMIN_TOKEN", ADMIN_TOKEN)
    app.state.db_pool = pool
    affiliate = await create_affiliate(pool, "OMAR10", "dsc_omar_admin", "Omar")
    await upsert_installation(pool, 920, "acme")
    await record_referral(pool, 920, affiliate["id"])
    await record_commission(
        pool, affiliate["id"], 920, "txn_admin_1", Decimal("4.05"), datetime.now(timezone.utc)
    )

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.get(
            "/admin/affiliates", headers={"Authorization": f"Bearer {ADMIN_TOKEN}"}
        )

    assert response.status_code == 200
    rows = {row["id"]: row for row in response.json()["affiliates"]}
    assert rows[affiliate["id"]]["referral_count"] == 1
    assert rows[affiliate["id"]]["total_owed_usd"] == 4.05


@pytest.mark.asyncio
async def test_mark_paid_requires_bearer_token(pool, monkeypatch):
    monkeypatch.setenv("AFFILIATE_ADMIN_TOKEN", ADMIN_TOKEN)
    app.state.db_pool = pool
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.post("/admin/affiliates/1/mark-paid")
    assert response.status_code == 401


@pytest.mark.asyncio
async def test_mark_paid_marks_commissions_and_reflects_in_report(pool, monkeypatch):
    monkeypatch.setenv("AFFILIATE_ADMIN_TOKEN", ADMIN_TOKEN)
    app.state.db_pool = pool
    affiliate = await create_affiliate(pool, "PIA10", "dsc_pia_admin", "Pia")
    await upsert_installation(pool, 921, "acme")
    await record_referral(pool, 921, affiliate["id"])
    await record_commission(
        pool, affiliate["id"], 921, "txn_admin_2", Decimal("4.05"), datetime.now(timezone.utc)
    )

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        mark_response = await client.post(
            f"/admin/affiliates/{affiliate['id']}/mark-paid",
            headers={"Authorization": f"Bearer {ADMIN_TOKEN}"},
        )
        list_response = await client.get(
            "/admin/affiliates", headers={"Authorization": f"Bearer {ADMIN_TOKEN}"}
        )

    assert mark_response.status_code == 200
    assert mark_response.json() == {"marked_paid_count": 1}
    rows = {row["id"]: row for row in list_response.json()["affiliates"]}
    assert rows[affiliate["id"]]["total_owed_usd"] == 0
    assert rows[affiliate["id"]]["total_paid_usd"] == 4.05
