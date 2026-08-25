from app_server.email_templates import (
    deletion_otp_email,
    health_alert_email,
    payment_failed_email,
    subscription_canceled_email,
    weekly_digest_email,
    welcome_email,
)


def test_welcome_email_greets_by_login_and_includes_pricing_link():
    message = welcome_email("octocat")
    assert "octocat" in message["text"]
    assert "octocat" in message["html"]
    assert "pricing.html" in message["html"]
    for key in ("subject", "html", "text"):
        assert message[key]


def test_payment_failed_email_names_account_and_does_not_promise_a_grace_period():
    message = payment_failed_email("acme-corp")
    assert "acme-corp" in message["text"]
    assert "acme-corp" in message["html"]
    # Access is already fully revoked by the time this fires (no dunning
    # grace period) - the copy must say so, not imply access is still on.
    assert "resubscribe" in message["text"].lower()
    assert "pricing.html" in message["html"]


def test_deletion_otp_email_includes_the_code_and_a_10_minute_expiry():
    message = deletion_otp_email("acme-corp", "424242")
    assert "424242" in message["subject"]
    assert "424242" in message["html"]
    assert "424242" in message["text"]
    assert "acme-corp" in message["text"]
    assert "10 minutes" in message["text"]
    for key in ("subject", "html", "text"):
        assert message[key]


def test_health_alert_email_reuses_the_slack_alert_text_verbatim():
    # No parallel copywriting - the email carries exactly what
    # format_reachability_alert/format_latency_alert/format_shape_change_alert
    # already built for Slack/Teams, just re-rendered for email.
    alert_text = "*Aletheore*: endpoint down on `octocat/hello-world`\n`GET /users` is unreachable"
    message = health_alert_email(alert_text)
    assert alert_text in message["text"]
    for key in ("subject", "html", "text"):
        assert message[key]


def test_health_alert_email_converts_slack_markdown_to_html():
    alert_text = "*Aletheore*: endpoint down on `octocat/hello-world`\n`GET /users` is unreachable"
    message = health_alert_email(alert_text)
    assert "<strong>Aletheore</strong>" in message["html"]
    assert "<code" in message["html"] and "octocat/hello-world" in message["html"]
    # The literal markdown characters must not leak into the rendered HTML.
    assert "*Aletheore*" not in message["html"]
    assert "`octocat/hello-world`" not in message["html"]


def test_subscription_canceled_email_names_account_and_lists_what_is_lost():
    message = subscription_canceled_email("acme-corp")
    assert "acme-corp" in message["text"]
    assert "acme-corp" in message["html"]
    assert "AIRview" in message["text"]
    assert "pricing.html" in message["html"]


def test_weekly_digest_email_reports_real_numbers_when_active():
    message = weekly_digest_email(
        account_login="acme-corp",
        scans_this_week=3,
        endpoints_reachable=4,
        endpoints_total=5,
        llm_spend_month_to_date=12.34,
        flash_reviews_month_to_date=7,
    )
    assert "acme-corp" in message["text"]
    assert "3 scans" in message["text"]
    assert "4/5 reachable" in message["text"]
    assert "$12.34" in message["text"]
    assert "7 automated PR reviews" in message["text"]


def test_weekly_digest_email_singular_scan_and_review_wording():
    message = weekly_digest_email(
        account_login="acme-corp",
        scans_this_week=1,
        endpoints_reachable=1,
        endpoints_total=1,
        llm_spend_month_to_date=0.5,
        flash_reviews_month_to_date=1,
    )
    assert "1 scan run this week" in message["text"]
    assert "1 automated PR review." in message["text"]


def test_weekly_digest_email_reads_naturally_with_zero_activity():
    # Sent unconditionally to re-engage quiet installs (see
    # digest_sends' migration comment) - every field must degrade to
    # something that reads as an invitation, not a broken report.
    message = weekly_digest_email(
        account_login="quiet-co",
        scans_this_week=0,
        endpoints_reachable=0,
        endpoints_total=0,
        llm_spend_month_to_date=0.0,
        flash_reviews_month_to_date=0,
    )
    assert "No scans run this week" in message["text"]
    assert "No endpoints being monitored yet" in message["text"]
    assert "0/0 reachable" not in message["text"]
    assert "$0.00" in message["text"]
