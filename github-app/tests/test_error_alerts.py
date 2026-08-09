from app_server import error_alerts
from app_server.error_alerts import send_error_alert


def test_skips_when_resend_api_key_not_configured(monkeypatch):
    monkeypatch.delenv("RESEND_API_KEY", raising=False)
    monkeypatch.setattr(error_alerts, "_last_alert_at", {})

    def _fail_if_called(*a, **k):
        raise AssertionError("should not attempt to send without a configured API key")

    monkeypatch.setattr(error_alerts, "send_transactional_email", _fail_if_called)

    send_error_alert("app_server", ValueError("boom"))


def test_sends_alert_with_source_and_exception_details(monkeypatch):
    monkeypatch.setenv("RESEND_API_KEY", "re_test_key")
    monkeypatch.setattr(error_alerts, "_last_alert_at", {})

    sent = []
    monkeypatch.setattr(
        error_alerts,
        "send_transactional_email",
        lambda api_key, from_addr, reply_to, to, subject, html, text: sent.append(
            {"api_key": api_key, "to": to, "subject": subject, "text": text}
        ),
    )

    send_error_alert("run_flash_review_job", ValueError("boom"), "job_id=abc123")

    assert len(sent) == 1
    assert sent[0]["api_key"] == "re_test_key"
    assert "run_flash_review_job" in sent[0]["subject"]
    assert "ValueError" in sent[0]["subject"]
    assert "boom" in sent[0]["text"]
    assert "job_id=abc123" in sent[0]["text"]


def test_rate_limits_repeated_alerts_for_the_same_source_and_error_type(monkeypatch):
    monkeypatch.setenv("RESEND_API_KEY", "re_test_key")
    monkeypatch.setattr(error_alerts, "_last_alert_at", {})

    sent = []
    monkeypatch.setattr(
        error_alerts,
        "send_transactional_email",
        lambda *a, **k: sent.append(1),
    )

    send_error_alert("app_server", ValueError("first"))
    send_error_alert("app_server", ValueError("second"))

    assert len(sent) == 1


def test_does_not_rate_limit_a_different_exception_type_from_the_same_source(monkeypatch):
    monkeypatch.setenv("RESEND_API_KEY", "re_test_key")
    monkeypatch.setattr(error_alerts, "_last_alert_at", {})

    sent = []
    monkeypatch.setattr(
        error_alerts,
        "send_transactional_email",
        lambda *a, **k: sent.append(1),
    )

    send_error_alert("app_server", ValueError("a"))
    send_error_alert("app_server", KeyError("b"))

    assert len(sent) == 2


def test_never_raises_when_sending_the_alert_itself_fails(monkeypatch):
    monkeypatch.setenv("RESEND_API_KEY", "re_test_key")
    monkeypatch.setattr(error_alerts, "_last_alert_at", {})

    def _boom(*a, **k):
        raise RuntimeError("Resend is down")

    monkeypatch.setattr(error_alerts, "send_transactional_email", _boom)

    send_error_alert("app_server", ValueError("boom"))  # must not raise
