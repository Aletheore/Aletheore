import json
import socket
from unittest.mock import MagicMock, patch

import pytest

from app_server.url_validation import UnsafeURLError
from scan_worker.slack import (
    _detect_platform,
    _slack_markdown_to_adaptive_card_markdown,
    _to_teams_payload,
    format_latency_alert,
    format_reachability_alert,
    format_runtime_error_alert,
    format_shape_change_alert,
    format_slack_message,
    send_health_alert,
    send_slack_alert,
)


def _mock_opener(calls: list):
    # Every webhook send is now pinned to a resolved IP (see slack.py's
    # own docstring on _post_to_webhook) - real tests mock at that
    # boundary (validate_and_pin_https_url + opener_for) instead of an
    # httpx transport, so they exercise the real pin-then-POST wiring
    # without doing real DNS/network I/O.
    def fake_open(request, timeout=None):
        calls.append(request)
        response = MagicMock()
        response.__enter__.return_value = response
        response.__exit__.return_value = False
        return response

    opener = MagicMock()
    opener.open.side_effect = fake_open
    return opener


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
    with patch(
        "scan_worker.slack.validate_and_pin_https_url",
        return_value=("https://hooks.slack.com/services/x", "93.184.216.34"),
    ) as mock_pin, patch("scan_worker.slack.opener_for", return_value=_mock_opener(calls)) as mock_opener_for:
        send_slack_alert(
            "https://hooks.slack.com/services/x",
            _diff_with_new_secret(),
            "octocat/hello-world",
            42,
        )
    mock_pin.assert_called_once_with("https://hooks.slack.com/services/x")
    mock_opener_for.assert_called_once_with("93.184.216.34")
    assert len(calls) == 1
    assert calls[0].full_url == "https://hooks.slack.com/services/x"


def test_send_slack_alert_raises_when_the_webhook_url_no_longer_resolves_safely():
    # Real regression this guards: a webhook URL that resolved to a public
    # IP when saved can have its DNS repointed at an internal address
    # before the next alert fires - the SSRF/DNS-rebinding gap #204 closed
    # for the health-check sweep but this call path never got until now.
    with patch(
        "scan_worker.slack.validate_and_pin_https_url",
        side_effect=UnsafeURLError("'internal.example.com' resolves to a disallowed address"),
    ):
        with pytest.raises(UnsafeURLError):
            send_slack_alert(
                "https://internal.example.com/webhook",
                _diff_with_new_secret(),
                "octocat/hello-world",
                42,
            )


def test_format_reachability_alert_down():
    evidence_resolution = {
        "symbol": "list_users",
        "owner": ["@api-team"],
        "commit": {"sha": "abcdef123456", "subject": "change user route"},
        "dependency": ["UserService"],
        "risk": [{"category": "availability", "severity": "high", "summary": "recently unreachable"}],
    }
    body = format_reachability_alert(
        "octocat/hello-world",
        "GET",
        "/api/users",
        "controllers/user.controller.ts",
        42,
        now_reachable=False,
        evidence_resolution=evidence_resolution,
    )
    assert "down" in body["text"]
    assert "octocat/hello-world" in body["text"]
    assert "/api/users" in body["text"]
    assert "controllers/user.controller.ts:42" in body["text"]
    assert "list_users" in body["text"]
    assert "@api-team" in body["text"]
    assert "abcdef12" in body["text"]
    assert "UserService" in body["text"]
    assert "recently unreachable" in body["text"]
    # Down is the "blaring, can't-ignore" case - Pushover's emergency
    # priority (repeats until acknowledged), not a single quiet ping.
    assert body["pushover_priority"] == 2


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
    assert body["pushover_priority"] == 0


def test_format_latency_alert_over():
    evidence_resolution = {
        "symbol": "list_users",
        "owner": "@api-team",
        "dependency": ["UserService"],
        "risk": [{"category": "dependency", "severity": "medium", "summary": "slow dependency"}],
    }
    body = format_latency_alert(
        "octocat/hello-world",
        "GET",
        "/api/users",
        "controllers/user.controller.ts",
        42,
        4120.0,
        3000,
        now_over=True,
        evidence_resolution=evidence_resolution,
    )
    assert "slow" in body["text"]
    assert "4120" in body["text"]
    assert "3000" in body["text"]
    assert "controllers/user.controller.ts:42" in body["text"]
    assert "list_users" in body["text"]
    assert "@api-team" in body["text"]
    assert "UserService" in body["text"]
    assert "slow dependency" in body["text"]


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


def test_format_shape_change_alert_reports_added_and_dropped_keys():
    body = format_shape_change_alert(
        "octocat/hello-world",
        "GET",
        "/api/users",
        "controllers/user.controller.ts",
        42,
        prior_shape=["email", "id", "name"],
        current_shape=["id", "name", "role"],
    )

    assert "response shape changed" in body["text"]
    assert "added keys: role" in body["text"]
    assert "dropped keys: email" in body["text"]
    assert "controllers/user.controller.ts:42" in body["text"]


def test_format_shape_change_alert_includes_evidence_context():
    evidence_resolution = {
        "commit": {"sha": "abcdef123456", "subject": "drop email from response"},
    }
    body = format_shape_change_alert(
        "octocat/hello-world",
        "GET",
        "/api/users",
        None,
        None,
        prior_shape=["email", "id"],
        current_shape=["id"],
        evidence_resolution=evidence_resolution,
    )

    assert "Recent commit: `abcdef12`" in body["text"]


def test_send_health_alert_posts_message():
    calls = []
    with patch(
        "scan_worker.slack.validate_and_pin_https_url",
        return_value=("https://hooks.slack.com/x", "93.184.216.34"),
    ), patch("scan_worker.slack.opener_for", return_value=_mock_opener(calls)):
        send_health_alert("https://hooks.slack.com/x", {"text": "test"})
    assert len(calls) == 1


def test_send_health_alert_raises_when_the_webhook_url_no_longer_resolves_safely():
    # Same regression coverage as send_slack_alert's own test above, for
    # this call path - the production alert dispatcher's own gap (worse
    # than the admin "test" button's, since it had zero validation at all
    # before this fix).
    with patch(
        "scan_worker.slack.validate_and_pin_https_url",
        side_effect=UnsafeURLError("'internal.example.com' resolves to a disallowed address"),
    ):
        with pytest.raises(UnsafeURLError):
            send_health_alert("https://internal.example.com/webhook", {"text": "test"})


def test_send_health_alert_end_to_end_refuses_a_webhook_that_resolves_internally():
    # Same shape as app_server/tests/test_url_validation.py's own DNS-
    # rebinding tests, but exercised through the real call path this
    # session's audit found had zero protection at all - only DNS
    # resolution and the actual network connection are mocked (real
    # socket.getaddrinfo and a real urllib opener.open would otherwise
    # both need live network I/O), everything else (validate_and_pin_
    # https_url, opener_for) runs for real, proving the actual wiring
    # refuses the connection rather than just that a mock says it does.
    def fake_addrinfo(*args, **kwargs):
        return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("169.254.169.254", 443))]

    with patch("app_server.url_validation.socket.getaddrinfo", fake_addrinfo):
        with pytest.raises(UnsafeURLError, match="disallowed"):
            send_health_alert("https://webhook.example.com/x", {"text": "test"})


def test_format_runtime_error_alert_includes_exception_and_location():
    evidence_resolution = {
        "symbol": "handle_request",
        "owner": ["@api-team"],
        "commit": {"sha": "abcdef123456", "subject": "change handler"},
        "dependency": ["UserService"],
    }
    body = format_runtime_error_alert(
        "octocat/hello-world",
        "ZeroDivisionError",
        "division by zero",
        "app/handler.py",
        42,
        method="GET",
        path="/v1/users",
        evidence_resolution=evidence_resolution,
    )

    assert "ZeroDivisionError" in body["text"]
    assert "division by zero" in body["text"]
    assert "app/handler.py:42" in body["text"]
    assert "GET /v1/users" in body["text"]
    assert "handle_request" in body["text"]
    assert "@api-team" in body["text"]
    assert "abcdef12" in body["text"]


def test_format_runtime_error_alert_handles_missing_method_and_path():
    body = format_runtime_error_alert(
        "octocat/hello-world", "ValueError", "bad input", "app/handler.py", 10, method="", path=""
    )

    assert "ValueError" in body["text"]
    assert "app/handler.py:10" in body["text"]


def test_detect_platform_recognizes_slack_url():
    assert _detect_platform("https://hooks.slack.com/services/T00/B00/xyz") == "slack"


def test_detect_platform_recognizes_teams_workflow_url():
    assert _detect_platform("https://prod-12.westus.logic.azure.com:443/workflows/abc/triggers/manual") == "teams"


def test_detect_platform_recognizes_classic_teams_connector_url():
    assert _detect_platform("https://outlook.office.com/webhook/abc") == "teams"


def test_detect_platform_defaults_to_slack_for_unknown_url():
    assert _detect_platform("https://example.com/my-webhook") == "slack"


def test_slack_markdown_to_adaptive_card_markdown_converts_bold():
    assert _slack_markdown_to_adaptive_card_markdown("*Aletheore*: new findings") == "**Aletheore**: new findings"


def test_slack_markdown_to_adaptive_card_markdown_leaves_code_spans_alone():
    text = "Secret: `a.py:1` (aws_key)"
    assert _slack_markdown_to_adaptive_card_markdown(text) == text


def test_to_teams_payload_wraps_text_in_adaptive_card():
    payload = _to_teams_payload({"text": "*Aletheore*: new findings on `octocat/hello-world` PR #42"})

    assert payload["type"] == "message"
    card = payload["attachments"][0]["content"]
    assert payload["attachments"][0]["contentType"] == "application/vnd.microsoft.card.adaptive"
    assert card["type"] == "AdaptiveCard"
    body_text = card["body"][0]["text"]
    assert "**Aletheore**" in body_text
    assert "octocat/hello-world" in body_text


def test_send_slack_alert_sends_adaptive_card_to_teams_webhook():
    calls = []
    teams_url = "https://prod-12.westus.logic.azure.com:443/workflows/abc/triggers/manual"
    with patch(
        "scan_worker.slack.validate_and_pin_https_url", return_value=(teams_url, "93.184.216.34")
    ), patch("scan_worker.slack.opener_for", return_value=_mock_opener(calls)):
        send_slack_alert(teams_url, _diff_with_new_secret(), "octocat/hello-world", 42)
    assert len(calls) == 1
    body = json.loads(calls[0].data)
    assert body["type"] == "message"
    assert body["attachments"][0]["contentType"] == "application/vnd.microsoft.card.adaptive"


def test_send_slack_alert_sends_plain_text_to_slack_webhook():
    calls = []
    with patch(
        "scan_worker.slack.validate_and_pin_https_url",
        return_value=("https://hooks.slack.com/services/x", "93.184.216.34"),
    ), patch("scan_worker.slack.opener_for", return_value=_mock_opener(calls)):
        send_slack_alert(
            "https://hooks.slack.com/services/x", _diff_with_new_secret(), "octocat/hello-world", 42
        )
    assert len(calls) == 1
    body = json.loads(calls[0].data)
    assert "text" in body
    assert "attachments" not in body


def test_send_health_alert_sends_adaptive_card_to_teams_webhook():
    calls = []
    teams_url = "https://prod-12.westus.logic.azure.com:443/workflows/abc/triggers/manual"
    with patch(
        "scan_worker.slack.validate_and_pin_https_url", return_value=(teams_url, "93.184.216.34")
    ), patch("scan_worker.slack.opener_for", return_value=_mock_opener(calls)):
        send_health_alert(teams_url, {"text": "*Aletheore*: endpoint down"})
    assert len(calls) == 1
    body = json.loads(calls[0].data)
    assert body["attachments"][0]["contentType"] == "application/vnd.microsoft.card.adaptive"
