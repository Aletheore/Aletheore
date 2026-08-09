from app_server.email_templates import (
    payment_failed_email,
    subscription_canceled_email,
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
