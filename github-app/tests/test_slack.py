import httpx

from scan_worker.slack import (
    format_latency_alert,
    format_reachability_alert,
    format_slack_message,
    send_health_alert,
    send_slack_alert,
)


def _diff_with_new_secret():
    return {
        "secrets": {"new": [{"path": "a.py", "line": 1, "pattern": "aws_key"}], "resolved": []},
        "history_secrets": {"new": [], "resolved": []},
        "vulnerabilities": {"new": [], "resolved": []},
        "layer_violations": {"new": [], "resolved": []},
    }


def test_format_slack_message_mentions_repo_and_pr():
    body = format_slack_message(_diff_with_new_secret(), "octocat/hello-world", 42)
    assert "octocat/hello-world" in body["text"]
    assert "42" in body["text"]
    assert "a.py:1" in body["text"]


def test_send_slack_alert_posts_to_webhook_url():
    calls = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        return httpx.Response(200)

    client = httpx.Client(transport=httpx.MockTransport(handler))
    send_slack_alert(
        "https://hooks.slack.com/services/x",
        _diff_with_new_secret(),
        "octocat/hello-world",
        42,
        http_client=client,
    )
    assert len(calls) == 1
    assert str(calls[0].url) == "https://hooks.slack.com/services/x"


def test_format_reachability_alert_down():
    body = format_reachability_alert(
        "octocat/hello-world",
        "GET",
        "/api/users",
        "controllers/user.controller.ts",
        42,
        now_reachable=False,
    )
    assert "down" in body["text"]
    assert "octocat/hello-world" in body["text"]
    assert "/api/users" in body["text"]
    assert "controllers/user.controller.ts:42" in body["text"]


def test_format_reachability_alert_recovered():
    body = format_reachability_alert(
        "octocat/hello-world",
        "GET",
        "/api/users",
        "controllers/user.controller.ts",
        42,
        now_reachable=True,
    )
    assert "recovered" in body["text"]
    assert "controllers/user.controller.ts:42" in body["text"]


def test_format_latency_alert_over():
    body = format_latency_alert(
        "octocat/hello-world",
        "GET",
        "/api/users",
        "controllers/user.controller.ts",
        42,
        4120.0,
        3000,
        now_over=True,
    )
    assert "slow" in body["text"]
    assert "4120" in body["text"]
    assert "3000" in body["text"]
    assert "controllers/user.controller.ts:42" in body["text"]


def test_format_latency_alert_under():
    body = format_latency_alert(
        "octocat/hello-world",
        "GET",
        "/api/users",
        "controllers/user.controller.ts",
        42,
        850.0,
        3000,
        now_over=False,
    )
    assert "under threshold" in body["text"]
    assert "controllers/user.controller.ts:42" in body["text"]


def test_send_health_alert_posts_message():
    calls = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        return httpx.Response(200)

    client = httpx.Client(transport=httpx.MockTransport(handler))
    send_health_alert("https://hooks.slack.com/x", {"text": "test"}, http_client=client)
    assert len(calls) == 1
