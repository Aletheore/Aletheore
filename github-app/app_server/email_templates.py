"""Transactional email copy. Plain functions returning
{"subject", "html", "text"} - no templating engine, since these are a
handful of developer-authored, mostly-static messages with a few
interpolated variables, matching scan_worker/slack.py's format_* pattern
rather than pulling in a new dependency for it.

HTML is table-based (not flexbox/grid) with every style inlined, so it
renders consistently in Outlook desktop's Word engine as well as modern
webmail/mobile clients - not just the ones a browser preview would show.
"""

_LOGO_URL = "https://www.aletheore.com/assets/logo-mark.png"
_PRICING_URL = "https://aletheore.com/pricing.html"
_DASHBOARD_URL = "https://app.aletheore.com/dashboard"
_FONT = "-apple-system,BlinkMacSystemFont,'Segoe UI',Helvetica,Arial,sans-serif"

# Matches website/styles.css's --bg-cream/--text-primary/--accent palette,
# just flipped to a light card on a cream page background (dark-mode-only
# HTML email is a real risk - some clients strip backgrounds, others invert
# colors - so the brand's dark theme is represented via the header band and
# accent color rather than the whole message).
_BODY_BG = "#f3eee3"
_CARD_BG = "#ffffff"
_HEADER_BG = "#17140f"
_HEADER_TEXT = "#f3eee3"
_TEXT_PRIMARY = "#201b14"
_TEXT_MUTED = "#8a8377"
_TEXT_FAINT = "#a9a095"
_BORDER = "#e5e0d5"
_ACCENT = "#e0863a"

_FOOTER_TEXT = (
    "\n\n---\n"
    "Aletheore - evidence-grounded repository audits\n"
    "https://aletheore.com\n\n"
    "You're receiving this because you signed in to Aletheore on GitHub. "
    "Reply anytime - a person reads it."
)


def _button(label: str, url: str) -> str:
    return (
        f'<a href="{url}" style="display:inline-block;background:{_ACCENT};'
        f'color:#ffffff;text-decoration:none;font-weight:650;font-size:14px;'
        f'padding:12px 24px;border-radius:8px;font-family:{_FONT};">{label}</a>'
    )


def _shell(preheader: str, body_html: str) -> str:
    # The hidden preheader div is what shows next to the subject line in an
    # inbox list (Gmail, Apple Mail, etc) before the message is opened - the
    # zero-width joiners pad it out so the client doesn't fall back to
    # rendering the first visible line of the body instead.
    return (
        f'<div style="display:none;max-height:0;overflow:hidden;mso-hide:all;">'
        f"{preheader}&nbsp;&zwnj;&nbsp;&zwnj;&nbsp;&zwnj;&nbsp;&zwnj;</div>"
        f'<table role="presentation" width="100%" border="0" cellpadding="0" '
        f'cellspacing="0" style="background:{_BODY_BG};padding:32px 16px;'
        f'font-family:{_FONT};">'
        f'<tr><td align="center">'
        f'<table role="presentation" width="100%" border="0" cellpadding="0" '
        f'cellspacing="0" style="max-width:560px;background:{_CARD_BG};'
        f'border:1px solid {_BORDER};border-radius:12px;overflow:hidden;">'
        f'<tr><td style="background:{_HEADER_BG};padding:22px 32px;">'
        f'<table role="presentation" border="0" cellpadding="0" cellspacing="0">'
        f"<tr>"
        f'<td style="padding-right:9px;"><img src="{_LOGO_URL}" width="24" '
        f'height="24" alt="Aletheore" style="display:block;border-radius:6px;">'
        f"</td>"
        f'<td style="color:{_HEADER_TEXT};font-size:16px;font-weight:700;'
        f'font-family:{_FONT};">Aletheore</td>'
        f"</tr></table>"
        f"</td></tr>"
        f'<tr><td style="padding:32px;color:{_TEXT_PRIMARY};font-size:15px;'
        f'line-height:1.6;font-family:{_FONT};">{body_html}</td></tr>'
        f'<tr><td style="padding:20px 32px;background:{_BODY_BG};'
        f'border-top:1px solid {_BORDER};color:{_TEXT_MUTED};font-size:12px;'
        f'line-height:1.7;font-family:{_FONT};">'
        f"Aletheore &mdash; evidence-grounded repository audits<br>"
        f'<a href="https://aletheore.com" style="color:{_TEXT_MUTED};">'
        f"aletheore.com</a>&nbsp;&middot;&nbsp;"
        f'<a href="mailto:support@aletheore.com" style="color:{_TEXT_MUTED};">'
        f"support@aletheore.com</a><br>"
        f'<span style="color:{_TEXT_FAINT};">You\'re receiving this because you '
        f"signed in to Aletheore on GitHub. Reply anytime &mdash; a person reads "
        f"it.</span>"
        f"</td></tr>"
        f"</table>"
        f"</td></tr>"
        f"</table>"
    )


def welcome_email(github_login: str) -> dict:
    subject = "Welcome to Aletheore"
    preheader = "Free deterministic scans today. AI-powered audits and PR reviews when you're ready."
    text = (
        f"Hi {github_login},\n\n"
        "Welcome to Aletheore. Community is free forever - run `aletheore scan .` "
        "on any repo to get evidence-grounded findings on secrets, vulnerabilities, "
        "dead code, licenses, and live endpoints. No LLM in the loop, no black box.\n\n"
        "When you're ready for more, Aletheore AIR adds managed AI audits, AIRview "
        "architecture maps, automated PR reviews, and endpoint health monitoring - "
        "installed once on your GitHub org and running in the background.\n\n"
        "See Aletheore AIR pricing: https://aletheore.com/pricing.html\n\n"
        "Questions, bug reports, or anything looks off - just reply. A person reads "
        "every message."
        f"{_FOOTER_TEXT}"
    )
    body_html = (
        f'<p style="margin:0 0 16px;">Hi {github_login},</p>'
        '<p style="margin:0 0 16px;">Welcome to Aletheore. Community is free '
        'forever &mdash; run <code style="background:#f3eee3;padding:2px 6px;'
        'border-radius:4px;">aletheore scan .</code> on any repo to get '
        'evidence-grounded findings on secrets, vulnerabilities, dead code, '
        'licenses, and live endpoints. No LLM in the loop, no black box.</p>'
        '<p style="margin:0 0 24px;">When you\'re ready for more, Aletheore AIR '
        'adds managed AI audits, AIRview architecture maps, automated PR reviews, '
        'and endpoint health monitoring &mdash; installed once on your GitHub org '
        'and running in the background.</p>'
        f'<p style="margin:0 0 24px;">{_button("See Aletheore AIR pricing", _PRICING_URL)}</p>'
        f'<p style="margin:0;color:{_TEXT_MUTED};font-size:14px;">Questions, bug '
        'reports, or anything looks off &mdash; just reply. A person reads every '
        'message.</p>'
    )
    html = _shell(preheader, body_html)
    return {"subject": subject, "html": html, "text": text}


def payment_failed_email(account_login: str) -> dict:
    # Access is already fully revoked by the time this fires (see
    # webhooks/paddle.py - there's no dunning-aware grace period, the
    # subscription downgrades to free the moment Paddle reports
    # past_due), so this can't honestly say "update your card before you
    # lose access" - it's already lost. Resubscribing via checkout is the
    # actual fix, not a customer-portal payment-method update.
    subject = "Your Aletheore AIR payment failed"
    preheader = f"{account_login} is back on the free Community plan until you resubscribe."
    text = (
        "Hi,\n\n"
        f"A payment for {account_login}'s Aletheore AIR subscription didn't go "
        "through, and the account has moved back to the free Community plan.\n\n"
        "Your scan history, settings, and endpoint targets are untouched - "
        "resubscribing restores AIR immediately: managed AI audits, AIRview, "
        "automated PR reviews, and endpoint monitoring.\n\n"
        "Resubscribe here: https://aletheore.com/pricing.html\n\n"
        "If this looks wrong, just reply to this email and we'll sort it out."
        f"{_FOOTER_TEXT}"
    )
    body_html = (
        '<p style="margin:0 0 16px;">Hi,</p>'
        f'<p style="margin:0 0 16px;">A payment for <strong>{account_login}</strong>'
        '\'s Aletheore AIR subscription didn\'t go through, and the account has '
        'moved back to the free Community plan.</p>'
        '<p style="margin:0 0 24px;">Your scan history, settings, and endpoint '
        'targets are untouched &mdash; resubscribing restores AIR immediately: '
        'managed AI audits, AIRview, automated PR reviews, and endpoint '
        'monitoring.</p>'
        f'<p style="margin:0 0 24px;">{_button("Resubscribe to Aletheore AIR", _PRICING_URL)}</p>'
        f'<p style="margin:0;color:{_TEXT_MUTED};font-size:14px;">If this looks '
        'wrong, just reply to this email and we\'ll sort it out.</p>'
    )
    html = _shell(preheader, body_html)
    return {"subject": subject, "html": html, "text": text}


def weekly_digest_email(
    account_login: str,
    scans_this_week: int,
    endpoints_reachable: int,
    endpoints_total: int,
    llm_spend_month_to_date: float,
    flash_reviews_month_to_date: int,
) -> dict:
    # Sent unconditionally to every paid installation, including quiet
    # ones (see digest_sends' migration comment - this is meant to
    # re-engage installs that have gone quiet, not just report on active
    # ones), so every line needs copy that reads naturally at zero too.
    if scans_this_week > 0:
        scan_line = f"{scans_this_week} scan{'s' if scans_this_week != 1 else ''} run this week."
    else:
        scan_line = (
            "No scans run this week - push to a repo, or run "
            "`aletheore audit . --managed`, and it'll show up here next week."
        )

    if endpoints_total > 0:
        health_line = f"Endpoint monitoring: {endpoints_reachable}/{endpoints_total} reachable right now."
    else:
        health_line = (
            "No endpoints being monitored yet - add one in Settings to catch "
            "outages before your users do."
        )

    review_word = "review" if flash_reviews_month_to_date == 1 else "reviews"
    spend_line = (
        f"${llm_spend_month_to_date:.2f} in AI spend this month, across "
        f"{flash_reviews_month_to_date} automated PR {review_word}."
    )

    subject = f"Your Aletheore weekly digest for {account_login}"
    preheader = f"{scan_line} {health_line}"
    text = (
        "Hi,\n\n"
        f"Here's what happened on {account_login}'s Aletheore AIR this week:\n\n"
        f"- {scan_line}\n"
        f"- {health_line}\n"
        f"- {spend_line}\n\n"
        "Open your dashboard: https://app.aletheore.com/dashboard\n\n"
        "Don't want these emails? Reply and let us know - we'll turn them off."
        f"{_FOOTER_TEXT}"
    )
    body_html = (
        '<p style="margin:0 0 16px;">Hi,</p>'
        f'<p style="margin:0 0 16px;">Here\'s what happened on '
        f'<strong>{account_login}</strong>\'s Aletheore AIR this week:</p>'
        f'<ul style="margin:0 0 24px;padding-left:20px;">'
        f'<li style="margin-bottom:8px;">{scan_line}</li>'
        f'<li style="margin-bottom:8px;">{health_line}</li>'
        f'<li>{spend_line}</li>'
        '</ul>'
        f'<p style="margin:0 0 24px;">{_button("Open your dashboard", _DASHBOARD_URL)}</p>'
        f'<p style="margin:0;color:{_TEXT_MUTED};font-size:14px;">Don\'t want these '
        'emails? Reply and let us know &mdash; we\'ll turn them off.</p>'
    )
    html = _shell(preheader, body_html)
    return {"subject": subject, "html": html, "text": text}


def subscription_canceled_email(account_login: str) -> dict:
    subject = "Sorry to see you go"
    preheader = f"{account_login} is back on Community. Your history and settings are still here."
    text = (
        "Hi,\n\n"
        f"{account_login}'s Aletheore AIR subscription has been canceled and the "
        "account is back on the free Community plan. Your scan history and "
        "settings are still there if you come back.\n\n"
        "Here's what's paused: managed AI audits, AIRview architecture maps, "
        "AI-generated Docs, automated PR reviews, Slack/Teams alerts, and "
        "endpoint health monitoring. The full deterministic CLI scanner stays "
        "free, forever, on Community.\n\n"
        "Resubscribe here: https://aletheore.com/pricing.html\n\n"
        "Canceled by mistake, or something didn't work the way you expected? "
        "Just reply - we'd genuinely like to know why."
        f"{_FOOTER_TEXT}"
    )
    body_html = (
        '<p style="margin:0 0 16px;">Hi,</p>'
        f'<p style="margin:0 0 16px;"><strong>{account_login}</strong>\'s Aletheore '
        'AIR subscription has been canceled and the account is back on the free '
        'Community plan. Your scan history and settings are still there if you '
        'come back.</p>'
        '<p style="margin:0 0 24px;">Here\'s what\'s paused: managed AI audits, '
        'AIRview architecture maps, AI-generated Docs, automated PR reviews, '
        'Slack/Teams alerts, and endpoint health monitoring. The full '
        'deterministic CLI scanner stays free, forever, on Community.</p>'
        f'<p style="margin:0 0 24px;">{_button("Resubscribe to Aletheore AIR", _PRICING_URL)}</p>'
        f'<p style="margin:0;color:{_TEXT_MUTED};font-size:14px;">Canceled by '
        'mistake, or something didn\'t work the way you expected? Just reply '
        '&mdash; we\'d genuinely like to know why.</p>'
    )
    html = _shell(preheader, body_html)
    return {"subject": subject, "html": html, "text": text}
