import httpx

from scan_worker.slack import format_slack_message, send_slack_alert


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
