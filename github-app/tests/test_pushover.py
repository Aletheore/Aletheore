import httpx

from scan_worker.pushover import send_pushover_alert


def test_send_pushover_alert_posts_token_user_and_message():
    calls = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        return httpx.Response(200, json={"status": 1, "request": "abc"})

    client = httpx.Client(transport=httpx.MockTransport(handler))
    send_pushover_alert(
        "app-token-x",
        "user-key-y",
        {"text": "*Aletheore*: endpoint down on `octocat/hello-world`"},
        http_client=client,
    )

    assert len(calls) == 1
    body = calls[0].read().decode()
    assert "token=app-token-x" in body
    assert "user=user-key-y" in body
    # Slack markdown markers are stripped for a plain-text phone notification.
    assert "Aletheore" in body
    assert "*" not in body
    assert "`" not in body


def test_send_pushover_alert_defaults_to_normal_priority():
    calls = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        return httpx.Response(200, json={"status": 1})

    client = httpx.Client(transport=httpx.MockTransport(handler))
    send_pushover_alert("token", "user", {"text": "endpoint recovered"}, http_client=client)

    body = calls[0].read().decode()
    assert "priority=0" in body
    assert "retry=" not in body
    assert "expire=" not in body


def test_send_pushover_alert_sends_emergency_priority_with_retry_and_expire():
    calls = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        return httpx.Response(200, json={"status": 1})

    client = httpx.Client(transport=httpx.MockTransport(handler))
    send_pushover_alert(
        "token",
        "user",
        {"text": "endpoint down", "pushover_priority": 2},
        http_client=client,
    )

    body = calls[0].read().decode()
    assert "priority=2" in body
    assert "retry=60" in body
    assert "expire=3600" in body


def test_send_pushover_alert_raises_on_http_error():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(400, json={"status": 0, "errors": ["user identifier is invalid"]})

    client = httpx.Client(transport=httpx.MockTransport(handler))
    try:
        send_pushover_alert("token", "bad-user", {"text": "x"}, http_client=client)
    except httpx.HTTPStatusError:
        return
    raise AssertionError("expected send_pushover_alert to raise on a non-2xx response")
