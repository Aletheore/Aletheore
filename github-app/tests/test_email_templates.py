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


def test_welcome_email_mentions_both_paid_tiers_not_just_air():
    """Real bug: this was the one template #470's AIR-only-branding sweep
    missed - every new free signup was funneled exclusively toward AIR and
    never told the cheaper Flash tier exists at all."""
    message = welcome_email("octocat")
    assert "Aletheore Flash" in message["text"]
    assert "Aletheore AIR" in message["text"]
    assert "Aletheore Flash" in message["html"]
    assert "Aletheore AIR" in message["html"]


def test_payment_failed_email_names_account_and_does_not_promise_a_grace_period():
    message = payment_failed_email("acme-corp", "air")
    assert "acme-corp" in message["text"]
    assert "acme-corp" in message["html"]
    # Access is already fully revoked by the time this fires (no dunning
    # grace period) - the copy must say so, not imply access is still on.
    assert "resubscribe" in message["text"].lower()
    assert "pricing.html" in message["html"]
    assert "Aletheore AIR" in message["text"]


def test_payment_failed_email_names_flash_not_air_for_a_flash_downgrade():
    """Real bug: this used to hardcode "AIR" regardless of which plan was
    actually lost - a flash customer's failed payment produced an email
    naming AIR-exclusive features (AIRview, endpoint monitoring) they
    never had."""
    message = payment_failed_email("acme-corp", "flash")
    assert "Aletheore Flash" in message["text"]
    assert "Aletheore AIR" not in message["text"]
    assert "AIRview" not in message["text"]
    assert "endpoint monitoring" not in message["text"]
    assert "automated PR reviews" in message["text"]


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


def test_health_alert_email_escapes_html_in_repo_controlled_alert_text():
    # Aletheore's own Flash Review caught this on PR #390: alert_text is
    # built from format_reachability_alert/format_latency_alert/format_
    # shape_change_alert, which interpolate repo-controlled content (commit
    # subjects, symbol names, risk summaries) - a malicious repo could
    # smuggle live HTML into this transactional email's body. Same bug
    # class as the wiki markdown renderer already guards against (see
    # test_wiki_markdown_renders_untrusted_html_inert in
    # test_frontend_js_syntax.py) - escape first, only then promote the
    # *bold*/`code` markdown.
    alert_text = "*Aletheore*: endpoint down on `<img src=x onerror=alert(1)>`"
    message = health_alert_email(alert_text)
    # Every email already contains a legitimate <img> for the logo, so
    # check the specific attacker-controlled tag rather than the bare
    # "<img" substring.
    assert "<img src=x onerror" not in message["html"]
    assert "onerror" in message["html"]
    assert "&lt;img src=x onerror" in message["html"]
    # Escaping must not break the legitimate markdown-to-HTML conversion.
    assert "<strong>Aletheore</strong>" in message["html"]


def test_subscription_canceled_email_names_account_and_lists_what_is_lost():
    message = subscription_canceled_email("acme-corp", "air")
    assert "acme-corp" in message["text"]
    assert "acme-corp" in message["html"]
    assert "AIRview" in message["text"]
    assert "pricing.html" in message["html"]


def test_subscription_canceled_email_names_flash_not_air_for_a_flash_cancellation():
    message = subscription_canceled_email("acme-corp", "flash")
    assert "Aletheore Flash" in message["text"]
    assert "Aletheore AIR" not in message["text"]
    assert "AIRview" not in message["text"]
    assert "endpoint health monitoring" not in message["text"]
    assert "automated PR reviews" in message["text"]


def test_weekly_digest_email_reports_real_numbers_when_active():
    message = weekly_digest_email(
        account_login="acme-corp",
        plan="air",
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
        plan="air",
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
        plan="air",
        scans_this_week=0,
        endpoints_reachable=0,
        endpoints_total=0,
        llm_spend_month_to_date=0.0,
        flash_reviews_month_to_date=0,
    )
    assert "No scans run this week" in message["text"]
    assert "No endpoints being monitored yet" in message["text"]
    assert "0/0 reachable" not in message["text"]


def test_weekly_digest_email_omits_endpoint_monitoring_and_dashboard_for_flash():
    """Real bug: this used to unconditionally promote endpoint monitoring
    and a managed dashboard - both AIR-exclusive - to every paid
    installation including flash, which has neither."""
    message = weekly_digest_email(
        account_login="acme-corp",
        plan="flash",
        scans_this_week=3,
        endpoints_reachable=0,
        endpoints_total=0,
        llm_spend_month_to_date=1.23,
        flash_reviews_month_to_date=5,
    )
    assert "Aletheore Flash" in message["text"]
    assert "Aletheore AIR" not in message["text"]
    assert "Endpoint monitoring" not in message["text"]
    assert "No endpoints being monitored yet" not in message["text"]
    assert "app.aletheore.com/dashboard" not in message["text"]
    assert "Manage your subscription" in message["text"]
    assert "pricing.html" in message["text"]
    assert "$1.23" in message["text"]
