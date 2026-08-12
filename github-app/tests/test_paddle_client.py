from unittest.mock import MagicMock

import httpx
import pytest

from app_server.paddle_client import (
    PaddleAPIError,
    PaddleAPINotConfigured,
    create_discount,
    create_portal_session,
    get_subscription,
    update_subscription_items,
)


def _patch_pooled_client(monkeypatch, handler):
    client = httpx.Client(transport=httpx.MockTransport(handler))
    factory = MagicMock(return_value=client)
    monkeypatch.setattr("app_server.paddle_client.get_generic_http_client", factory)
    return factory


def test_get_subscription_requires_api_key():
    with pytest.raises(PaddleAPINotConfigured):
        get_subscription(None, "sub_123")


def test_create_discount_requires_api_key():
    with pytest.raises(PaddleAPINotConfigured):
        create_discount(None, "SARAH10", "Affiliate: Sarah")


def test_create_discount_sends_post_with_expected_body(monkeypatch):
    captured = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["method"] = request.method
        captured["path"] = request.url.path
        captured["timeout"] = request.extensions["timeout"]
        import json

        captured["body"] = json.loads(request.content)
        return httpx.Response(201, json={"data": {"id": "dsc_123", "code": "SARAH10"}})

    factory = _patch_pooled_client(monkeypatch, handler)

    result = create_discount("test_key", "SARAH10", "Affiliate: Sarah")

    factory.assert_called_once_with()
    assert result == {"id": "dsc_123", "code": "SARAH10"}
    assert captured["method"] == "POST"
    assert captured["path"] == "/discounts"
    assert set(captured["timeout"].values()) == {15.0}
    assert captured["body"] == {
        "description": "Affiliate: Sarah",
        "type": "percentage",
        "amount": "10",
        "code": "SARAH10",
        "recur": True,
        "maximum_recurring_intervals": 1,
        "enabled_for_checkout": True,
    }


def test_create_discount_wraps_http_errors(monkeypatch):
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(409, json={"error": "code already exists"})

    _patch_pooled_client(monkeypatch, handler)

    with pytest.raises(PaddleAPIError):
        create_discount("test_key", "SARAH10", "Affiliate: Sarah")


def test_create_discount_wraps_unexpected_response_shape(monkeypatch):
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(201, json={"unexpected": {"id": "dsc_123"}})

    _patch_pooled_client(monkeypatch, handler)

    with pytest.raises(PaddleAPIError, match="unexpected Paddle response shape"):
        create_discount("test_key", "SARAH10", "Affiliate: Sarah")


def test_create_portal_session_requires_api_key():
    with pytest.raises(PaddleAPINotConfigured):
        create_portal_session(None, "ctm_123")


def test_create_portal_session_sends_post_with_subscription_ids(monkeypatch):
    captured = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["method"] = request.method
        captured["path"] = request.url.path
        import json

        captured["body"] = json.loads(request.content)
        return httpx.Response(
            201,
            json={"data": {"id": "cpls_123", "customer_id": "ctm_123", "urls": {"general": {}}}},
        )

    factory = _patch_pooled_client(monkeypatch, handler)

    result = create_portal_session("test_key", "ctm_123", ["sub_123"])

    factory.assert_called_once_with()
    assert result == {"id": "cpls_123", "customer_id": "ctm_123", "urls": {"general": {}}}
    assert captured["method"] == "POST"
    assert captured["path"] == "/customers/ctm_123/portal-sessions"
    assert captured["body"] == {"subscription_ids": ["sub_123"]}


def test_create_portal_session_omits_subscription_ids_when_none_given(monkeypatch):
    captured = {}

    def handler(request: httpx.Request) -> httpx.Response:
        import json

        captured["body"] = json.loads(request.content)
        return httpx.Response(201, json={"data": {"id": "cpls_123", "urls": {"general": {}}}})

    _patch_pooled_client(monkeypatch, handler)

    create_portal_session("test_key", "ctm_123")

    assert captured["body"] == {}


def test_create_portal_session_wraps_http_errors(monkeypatch):
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(404, json={"error": "not found"})

    _patch_pooled_client(monkeypatch, handler)

    with pytest.raises(PaddleAPIError):
        create_portal_session("test_key", "ctm_123")


def test_update_subscription_items_requires_api_key():
    with pytest.raises(PaddleAPINotConfigured):
        update_subscription_items(None, "sub_123", [], "prorated_immediately")


def test_get_subscription_returns_data_field(monkeypatch):
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/subscriptions/sub_123"
        assert request.headers["authorization"] == "Bearer test_key"
        return httpx.Response(200, json={"data": {"id": "sub_123", "status": "active"}})

    factory = _patch_pooled_client(monkeypatch, handler)

    result = get_subscription("test_key", "sub_123")
    factory.assert_called_once_with()
    assert result == {"id": "sub_123", "status": "active"}


def test_get_subscription_wraps_http_errors(monkeypatch):
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(404, json={"error": "not found"})

    _patch_pooled_client(monkeypatch, handler)

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

    factory = _patch_pooled_client(monkeypatch, handler)

    items = [{"price_id": "pri_base", "quantity": 1}, {"price_id": "pri_seat", "quantity": 2}]
    result = update_subscription_items("test_key", "sub_123", items, "prorated_immediately")

    factory.assert_called_once_with()
    assert result == {"id": "sub_123"}
    assert captured["method"] == "PATCH"
    assert captured["path"] == "/subscriptions/sub_123"
    assert captured["body"]["items"] == items
    assert captured["body"]["proration_billing_mode"] == "prorated_immediately"


def test_update_subscription_items_wraps_http_errors(monkeypatch):
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(400, json={"error": "bad request"})

    _patch_pooled_client(monkeypatch, handler)

    with pytest.raises(PaddleAPIError):
        update_subscription_items("test_key", "sub_123", [], "prorated_immediately")
