import hashlib
import hmac
import json

import pytest
from httpx import ASGITransport, AsyncClient

from app_server.main import app, settings


def _signature(payload: bytes, secret: str) -> str:
    digest = hmac.new(secret.encode(), payload, hashlib.sha256).hexdigest()
    return f"sha256={digest}"


@pytest.mark.asyncio
async def test_webhook_rejects_invalid_signature():
    app.state.db_pool = object()
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.post(
            "/webhook",
            content=b"{}",
            headers={
                "X-Hub-Signature-256": "sha256=wrong",
                "X-GitHub-Event": "installation",
            },
        )
    assert response.status_code == 401


@pytest.mark.asyncio
async def test_webhook_dispatches_pull_request_enqueue(monkeypatch):
    app.state.db_pool = object()
    payload = {
        "action": "opened",
        "number": 9,
        "installation": {"id": 123},
        "repository": {"full_name": "octocat/hello-world"},
        "pull_request": {"base": {"sha": "aaa"}, "head": {"sha": "bbb"}},
    }
    body = json.dumps(payload).encode()
    called = {}

    async def fake_handle(payload_arg, redis_url):
        called["payload"] = payload_arg
        called["redis_url"] = redis_url

    monkeypatch.setattr("app_server.webhooks.pull_request.handle_pull_request_event", fake_handle)

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.post(
            "/webhook",
            content=body,
            headers={
                "X-Hub-Signature-256": _signature(body, settings.github_webhook_secret),
                "X-GitHub-Event": "pull_request",
            },
        )

    assert response.status_code == 200
    assert called["payload"]["number"] == 9
    assert called["redis_url"] == settings.redis_url
