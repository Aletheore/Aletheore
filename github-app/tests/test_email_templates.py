from app_server.email_templates import (
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
