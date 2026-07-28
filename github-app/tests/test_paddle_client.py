import httpx
import pytest

from app_server.paddle_client import (
    PaddleAPIError,
    PaddleAPINotConfigured,
    get_subscription,
    update_subscription_items,
)


def test_get_subscription_requires_api_key():
    with pytest.raises(PaddleAPINotConfigured):
        get_subscription(None, "sub_123")


def test_update_subscription_items_requires_api_key():
    with pytest.raises(PaddleAPINotConfigured):
        update_subscription_items(None, "sub_123", [], "prorated_immediately")


def test_get_subscription_returns_data_field(monkeypatch):
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/subscriptions/sub_123"
        assert request.headers["authorization"] == "Bearer test_key"
        return httpx.Response(200, json={"data": {"id": "sub_123", "status": "active"}})

    monkeypatch.setattr(httpx, "get", lambda url, headers: httpx.Client(transport=httpx.MockTransport(handler)).get(url, headers=headers))

    result = get_subscription("test_key", "sub_123")
    assert result == {"id": "sub_123", "status": "active"}


def test_get_subscription_wraps_http_errors(monkeypatch):
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(404, json={"error": "not found"})

    monkeypatch.setattr(httpx, "get", lambda url, headers: httpx.Client(transport=httpx.MockTransport(handler)).get(url, headers=headers))

    with pytest.raises(PaddleAPIError):
        get_subscription("test_key", "sub_123")


def test_update_subscription_items_sends_patch_with_items_and_proration(monkeypatch):
    captured = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["method"] = request.method
        captured["path"] = request.url.path
        import json

        captured["body"] = json.loads(request.content)
        return httpx.Response(200, json={"data": {"id": "sub_123"}})

    monkeypatch.setattr(
        httpx,
        "patch",
        lambda url, headers, json: httpx.Client(transport=httpx.MockTransport(handler)).patch(
            url, headers=headers, json=json
        ),
    )

    items = [{"price_id": "pri_base", "quantity": 1}, {"price_id": "pri_seat", "quantity": 2}]
    result = update_subscription_items("test_key", "sub_123", items, "prorated_immediately")

    assert result == {"id": "sub_123"}
    assert captured["method"] == "PATCH"
    assert captured["path"] == "/subscriptions/sub_123"
    assert captured["body"]["items"] == items
    assert captured["body"]["proration_billing_mode"] == "prorated_immediately"


def test_update_subscription_items_wraps_http_errors(monkeypatch):
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(400, json={"error": "bad request"})

    monkeypatch.setattr(
        httpx,
        "patch",
        lambda url, headers, json: httpx.Client(transport=httpx.MockTransport(handler)).patch(
            url, headers=headers, json=json
        ),
    )

    with pytest.raises(PaddleAPIError):
        update_subscription_items("test_key", "sub_123", [], "prorated_immediately")
