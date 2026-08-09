"""Transactional email copy. Plain functions returning
{"subject", "html", "text"} - no templating engine, since these are a
handful of developer-authored, mostly-static messages with a few
interpolated variables, matching scan_worker/slack.py's format_* pattern
rather than pulling in a new dependency for it.
"""

_FOOTER_TEXT = "\n\n---\nAletheore - evidence-grounded repository audits\nhttps://aletheore.com"
_FOOTER_HTML = (
    '<p style="margin-top:32px;padding-top:16px;border-top:1px solid #e5e0d5;'
    'color:#8a8377;font-size:13px;">Aletheore - evidence-grounded repository audits'
    '<br><a href="https://aletheore.com" style="color:#8a8377;">aletheore.com</a></p>'
)


def welcome_email(github_login: str) -> dict:
    subject = "Welcome to Aletheore"
    text = (
        f"Hi {github_login},\n\n"
        "You're in. Aletheore Community is free forever - run `aletheore scan .` "
        "on any repo to get evidence-grounded secrets, vulnerability, dead-code, "
        "and endpoint findings with no LLM involved.\n\n"
        "If you install the GitHub App, Aletheore AIR adds managed AI audits, "
        "AIRview architecture maps, automated PR reviews, and endpoint health "
        "monitoring: https://aletheore.com/pricing.html\n\n"
        "Questions or anything looks wrong - just reply to this email."
        f"{_FOOTER_TEXT}"
    )
    html = (
        f'<p>Hi {github_login},</p>'
        '<p>You\'re in. Aletheore Community is free forever - run '
        '<code>aletheore scan .</code> on any repo to get evidence-grounded '
        'secrets, vulnerability, dead-code, and endpoint findings with no LLM '
        'involved.</p>'
        '<p>If you install the GitHub App, Aletheore AIR adds managed AI audits, '
        'AIRview architecture maps, automated PR reviews, and endpoint health '
        'monitoring: <a href="https://aletheore.com/pricing.html">see pricing</a>.</p>'
        '<p>Questions or anything looks wrong - just reply to this email.</p>'
        f"{_FOOTER_HTML}"
    )
    return {"subject": subject, "html": html, "text": text}


def payment_failed_email(account_login: str) -> dict:
    # Access is already fully revoked by the time this fires (see
    # webhooks/paddle.py - there's no dunning-aware grace period, the
    # subscription downgrades to free the moment Paddle reports
    # past_due), so this can't honestly say "update your card before you
    # lose access" - it's already lost. Resubscribing via checkout is the
    # actual fix, not a customer-portal payment-method update.
    subject = "Your Aletheore AIR payment failed"
    text = (
        f"Hi,\n\n"
        f"A payment for {account_login}'s Aletheore AIR subscription failed, "
        "and the account has been moved back to the free Community plan.\n\n"
        "To restore AIR (managed audits, AIRview, automated PR reviews, endpoint "
        "monitoring), resubscribe here: https://aletheore.com/pricing.html\n\n"
        "If this seems wrong, just reply to this email and we'll sort it out."
        f"{_FOOTER_TEXT}"
    )
    html = (
        "<p>Hi,</p>"
        f"<p>A payment for <strong>{account_login}</strong>'s Aletheore AIR "
        "subscription failed, and the account has been moved back to the free "
        "Community plan.</p>"
        '<p>To restore AIR (managed audits, AIRview, automated PR reviews, '
        'endpoint monitoring), '
        '<a href="https://aletheore.com/pricing.html">resubscribe here</a>.</p>'
        "<p>If this seems wrong, just reply to this email and we'll sort it out.</p>"
        f"{_FOOTER_HTML}"
    )
    return {"subject": subject, "html": html, "text": text}


def subscription_canceled_email(account_login: str) -> dict:
    subject = "Sorry to see you go"
    text = (
        f"Hi,\n\n"
        f"{account_login}'s Aletheore AIR subscription has been canceled and the "
        "account is back on the free Community plan. Your scan history and "
        "settings are still there if you come back.\n\n"
        "What you're losing: managed AI audits, AIRview architecture maps, "
        "AI-generated Docs, automated PR reviews, Slack/Teams alerts, and "
        "endpoint health monitoring. Community still gets the full deterministic "
        "CLI scanner, free forever.\n\n"
        "If you canceled by mistake or something didn't work the way you "
        "expected, just reply to this email - or resubscribe any time: "
        "https://aletheore.com/pricing.html"
        f"{_FOOTER_TEXT}"
    )
    html = (
        "<p>Hi,</p>"
        f"<p><strong>{account_login}</strong>'s Aletheore AIR subscription has "
        "been canceled and the account is back on the free Community plan. Your "
        "scan history and settings are still there if you come back.</p>"
        "<p>What you're losing: managed AI audits, AIRview architecture maps, "
        "AI-generated Docs, automated PR reviews, Slack/Teams alerts, and "
        "endpoint health monitoring. Community still gets the full deterministic "
        "CLI scanner, free forever.</p>"
        "<p>If you canceled by mistake or something didn't work the way you "
        "expected, just reply to this email - or "
        '<a href="https://aletheore.com/pricing.html">resubscribe any time</a>.</p>'
        f"{_FOOTER_HTML}"
    )
    return {"subject": subject, "html": html, "text": text}
