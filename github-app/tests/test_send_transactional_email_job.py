from scan_worker.jobs import send_transactional_email_job


def test_skips_when_already_sent(monkeypatch):
    monkeypatch.setenv("RESEND_API_KEY", "re_test_key")
    monkeypatch.setattr("scan_worker.jobs.email_already_sent", lambda dsn, key: True)

    def _fail_if_called(*a, **k):
        raise AssertionError("should not attempt to send an already-sent email")

    monkeypatch.setattr("scan_worker.jobs.send_transactional_email", _fail_if_called)

    send_transactional_email_job("welcome:octocat", "welcome", "octocat", "o@example.com")


def test_skips_when_resend_api_key_not_configured(monkeypatch):
    monkeypatch.delenv("RESEND_API_KEY", raising=False)
    monkeypatch.setattr("scan_worker.jobs.email_already_sent", lambda dsn, key: False)

    def _fail_if_called(*a, **k):
        raise AssertionError("should not attempt to send without a configured API key")

    monkeypatch.setattr("scan_worker.jobs.send_transactional_email", _fail_if_called)

    send_transactional_email_job("welcome:octocat", "welcome", "octocat", "o@example.com")


def test_sends_with_rendered_template_and_records_on_success(monkeypatch):
    monkeypatch.setenv("RESEND_API_KEY", "re_test_key")
    monkeypatch.setattr("scan_worker.jobs.email_already_sent", lambda dsn, key: False)

    send_calls = []

    def _fake_send(api_key, from_addr, reply_to, to, subject, html, text):
        send_calls.append(
            {
                "api_key": api_key,
                "from_addr": from_addr,
                "reply_to": reply_to,
                "to": to,
                "subject": subject,
                "html": html,
                "text": text,
            }
        )
        return {"id": "msg_abc"}

    monkeypatch.setattr("scan_worker.jobs.send_transactional_email", _fake_send)

    record_calls = []
    monkeypatch.setattr(
        "scan_worker.jobs.record_sent_email",
        lambda dsn, dedupe_key, template_name, recipient, installation_id, resend_message_id: record_calls.append(
            (dedupe_key, template_name, recipient, installation_id, resend_message_id)
        ),
    )

    send_transactional_email_job(
        "welcome:octocat", "welcome", "octocat", "o@example.com", installation_id=42
    )

    assert len(send_calls) == 1
    assert send_calls[0]["api_key"] == "re_test_key"
    assert send_calls[0]["to"] == "o@example.com"
    assert "octocat" in send_calls[0]["html"]

    assert record_calls == [("welcome:octocat", "welcome", "o@example.com", 42, "msg_abc")]


def test_dict_template_arg_is_expanded_as_keyword_args(monkeypatch):
    # weekly_digest needs several values, unlike the single-string
    # templates (welcome) - a dict template_arg is expanded as **kwargs
    # into the render function rather than passed positionally. Since the
    # flash-tier fix, payment_failed/subscription_canceled also use a
    # dict (account_login + plan) - this test only needs to cover the
    # expansion mechanism once, via weekly_digest.
    monkeypatch.setenv("RESEND_API_KEY", "re_test_key")
    monkeypatch.setattr("scan_worker.jobs.email_already_sent", lambda dsn, key: False)

    send_calls = []
    monkeypatch.setattr(
        "scan_worker.jobs.send_transactional_email",
        lambda api_key, from_addr, reply_to, to, subject, html, text: send_calls.append(
            {"subject": subject, "text": text}
        )
        or {"id": "msg_digest"},
    )
    monkeypatch.setattr("scan_worker.jobs.record_sent_email", lambda *a, **k: None)

    send_transactional_email_job(
        "weekly_digest:1:2026-W32:o@example.com",
        "weekly_digest",
        {
            "account_login": "acme",
            "plan": "air",
            "scans_this_week": 2,
            "endpoints_reachable": 1,
            "endpoints_total": 1,
            "llm_spend_month_to_date": 1.0,
            "flash_reviews_month_to_date": 1,
        },
        "o@example.com",
    )

    assert len(send_calls) == 1
    assert "acme" in send_calls[0]["text"]
    assert "2 scans" in send_calls[0]["text"]


def test_unknown_template_name_is_skipped_with_a_warning_instead_of_raising(monkeypatch, caplog):
    # Previously a KeyError. The credit-balance emails
    # (credit_low_balance/credit_exhausted) are enqueued by
    # reserve_llm_spend_with_email_hooks on this branch, but their templates
    # land on the parallel dashboard/emails branch - so depending on merge
    # order this worker really can be handed a template name it doesn't have
    # yet. Unguarded, @log_job turns that KeyError into a failed RQ job plus
    # an error alert for EVERY low-balance/exhausted event, which is a much
    # worse failure mode than a missing email. The guard doesn't close the
    # template gap (the other branch's templates do); it makes this branch
    # deployable independently of merge order.
    import logging

    monkeypatch.setenv("RESEND_API_KEY", "re_test_key")
    monkeypatch.setattr("scan_worker.jobs.email_already_sent", lambda dsn, key: False)

    def _fail_if_called(*a, **k):
        raise AssertionError("should not attempt to send an unrenderable template")

    monkeypatch.setattr("scan_worker.jobs.send_transactional_email", _fail_if_called)
    monkeypatch.setattr("scan_worker.jobs.record_sent_email", _fail_if_called)

    with caplog.at_level(logging.WARNING, logger="scan_worker.jobs"):
        send_transactional_email_job(
            "credit_low_balance:42:1",
            "credit_low_balance",
            {"account_login": "acme"},
            "o@example.com",
            installation_id=42,
        )

    assert "no email template registered for credit_low_balance" in caplog.text
    # Nothing recorded either: the dedupe key must stay unclaimed so the
    # same event can send for real once the templates land.
    assert "installation_id=42" in caplog.text
