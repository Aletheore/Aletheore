from app_server import error_alerts
from app_server.error_alerts import send_error_alert
from app_server.redis_client import get_redis_client


def _clear_cooldown(*keys):
    client = get_redis_client()
    for key in keys:
        client.delete(error_alerts._ALERT_COOLDOWN_KEY_PREFIX + key)


def test_skips_when_resend_api_key_not_configured(monkeypatch):
    monkeypatch.delenv("RESEND_API_KEY", raising=False)
    _clear_cooldown("app_server:ValueError")

    def _fail_if_called(*a, **k):
        raise AssertionError("should not attempt to send without a configured API key")

    monkeypatch.setattr(error_alerts, "send_transactional_email", _fail_if_called)

    send_error_alert("app_server", ValueError("boom"))


def test_sends_alert_with_source_and_exception_details(monkeypatch):
    monkeypatch.setenv("RESEND_API_KEY", "re_test_key")
    _clear_cooldown("run_flash_review_job:ValueError")

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
    _clear_cooldown("app_server:ValueError")

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
    _clear_cooldown("app_server:ValueError", "app_server:KeyError")

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
    _clear_cooldown("app_server:ValueError")

    def _boom(*a, **k):
        raise RuntimeError("Resend is down")

    monkeypatch.setattr(error_alerts, "send_transactional_email", _boom)

    send_error_alert("app_server", ValueError("boom"))  # must not raise


def test_cooldown_survives_across_separate_should_alert_invocations_like_a_forked_job_would_see(monkeypatch):
    # Real regression test for the actual production bug: the old
    # process-local dict looked correct within a single Python process,
    # but every job dispatched through scan_worker.worker's RQ Worker runs
    # in a freshly forked child process - there is no single process for a
    # dict to survive across. Simulating that here isn't a real fork, but
    # it proves the durable-storage property that actually matters: two
    # independent calls with no shared Python state between them (nothing
    # here relies on module-level memory) still see the same cooldown,
    # because it lives in Redis, not in this process.
    monkeypatch.setenv("RESEND_API_KEY", "re_test_key")
    _clear_cooldown("health_sweep:RuntimeError")

    sent = []
    monkeypatch.setattr(error_alerts, "send_transactional_email", lambda *a, **k: sent.append(1))

    send_error_alert("health_sweep", RuntimeError("still stale"))
    # A second, independent call - the real bug this guards against sent a
    # fresh email on every one of these instead of exactly one per cooldown
    # window.
    send_error_alert("health_sweep", RuntimeError("still stale"))
    send_error_alert("health_sweep", RuntimeError("still stale"))

    assert len(sent) == 1


def test_should_alert_fails_open_when_redis_is_unreachable(monkeypatch):
    # A cooldown check that can't reach Redis must never be the reason a
    # real alert never sends - better to occasionally over-alert than to
    # silently swallow every error notification because Redis had a blip.
    def _boom():
        raise ConnectionError("redis unreachable")

    monkeypatch.setattr(error_alerts, "get_redis_client", _boom)

    assert error_alerts._should_alert("some:key") is True
