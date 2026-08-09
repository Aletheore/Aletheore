import json

import httpx
import pytest

from app_server.email_client import send_transactional_email


def test_send_transactional_email_posts_expected_payload():
    calls = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        return httpx.Response(200, json={"id": "msg_123"})

    client = httpx.Client(transport=httpx.MockTransport(handler))
    result = send_transactional_email(
        "re_test_key",
        "Aletheore <hello@notify.aletheore.com>",
        "support@aletheore.com",
        "octocat@example.com",
        "Welcome",
        "<p>hi</p>",
        "hi",
        http_client=client,
    )

    assert result == {"id": "msg_123"}
    assert len(calls) == 1
    request = calls[0]
    assert str(request.url) == "https://api.resend.com/emails"
    assert request.headers["authorization"] == "Bearer re_test_key"
    body = json.loads(request.content)
    assert body == {
        "from": "Aletheore <hello@notify.aletheore.com>",
        "to": ["octocat@example.com"],
        "reply_to": "support@aletheore.com",
        "subject": "Welcome",
        "html": "<p>hi</p>",
        "text": "hi",
    }


def test_send_transactional_email_raises_on_error_response():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(422, json={"message": "invalid"})

    client = httpx.Client(transport=httpx.MockTransport(handler))
    with pytest.raises(httpx.HTTPStatusError):
        send_transactional_email(
            "re_test_key",
            "Aletheore <hello@notify.aletheore.com>",
            "support@aletheore.com",
            "bad@",
            "Welcome",
            "<p>hi</p>",
            "hi",
            http_client=client,
        )
