"""Transactional email copy. Plain functions returning
{"subject", "html", "text"} - no templating engine, since these are a
handful of developer-authored, mostly-static messages with a few
interpolated variables, matching scan_worker/slack.py's format_* pattern
rather than pulling in a new dependency for it.

HTML is table-based (not flexbox/grid) with every style inlined, so it
renders consistently in Outlook desktop's Word engine as well as modern
webmail/mobile clients - not just the ones a browser preview would show.
"""

import html
import re

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

# Mirrors frontend.py's _PLAN_DISPLAY_NAMES - kept as its own small copy
# rather than imported, since this module is deliberately dependency-light
# (see the module docstring) and frontend.py is a large FastAPI route
# module with its own import surface.
_PLAN_DISPLAY_NAMES = {
    "free": "Aletheore Community",
    "flash": "Aletheore Flash",
    "air": "Aletheore AIR",
}


def _plan_display_name(plan: str) -> str:
    return _PLAN_DISPLAY_NAMES.get(plan, _PLAN_DISPLAY_NAMES["air"])


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
    # This is the first email every free signup receives, sent before the
    # account has picked (or even seen) a paid plan - so unlike the other
    # templates in this file, there's no installation `plan` to branch on
    # here. Real bug this fixes: this was the one template #470 missed when
    # it swept payment_failed/subscription_canceled/weekly_digest for the
    # same hardcoded-AIR-only gap - every new free user was funneled
    # exclusively toward AIR and never told the cheaper Flash tier (Aletheore
    # Flash: automated PR reviews only, no managed audits/AIRview/endpoint
    # monitoring) exists at all.
    subject = "Welcome to Aletheore"
    preheader = "Free deterministic scans today. AI-powered audits and PR reviews when you're ready."
    text = (
        f"Hi {github_login},\n\n"
        "Welcome to Aletheore. Community is free forever - run `aletheore scan .` "
        "on any repo to get evidence-grounded findings on secrets, vulnerabilities, "
        "dead code, licenses, and live endpoints. No LLM in the loop, no black box.\n\n"
        "When you're ready for more: Aletheore Flash adds automated AI PR reviews "
        "for a lower monthly cost, and Aletheore AIR adds managed AI audits, AIRview "
        "architecture maps, automated PR reviews, and endpoint health monitoring - "
        "installed once on your GitHub org and running in the background.\n\n"
        "See pricing and compare plans: https://aletheore.com/pricing.html\n\n"
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
        '<p style="margin:0 0 24px;">When you\'re ready for more: Aletheore Flash '
        'adds automated AI PR reviews for a lower monthly cost, and Aletheore AIR '
        'adds managed AI audits, AIRview architecture maps, automated PR reviews, '
        'and endpoint health monitoring &mdash; installed once on your GitHub org '
        'and running in the background.</p>'
        f'<p style="margin:0 0 24px;">{_button("Compare plans and pricing", _PRICING_URL)}</p>'
        f'<p style="margin:0;color:{_TEXT_MUTED};font-size:14px;">Questions, bug '
        'reports, or anything looks off &mdash; just reply. A person reads every '
        'message.</p>'
    )
    html = _shell(preheader, body_html)
    return {"subject": subject, "html": html, "text": text}


def payment_failed_email(account_login: str, plan: str) -> dict:
    # Access is already fully revoked by the time this fires (see
    # webhooks/paddle.py - there's no dunning-aware grace period, the
    # subscription downgrades to free the moment Paddle reports
    # past_due), so this can't honestly say "update your card before you
    # lose access" - it's already lost. Resubscribing via checkout is the
    # actual fix, not a customer-portal payment-method update.
    #
    # plan is the PLAN BEING LOST (paddle.py's previous_plan), not the
    # installation's current plan - by the time this fires the DB already
    # reads "free". Real bug this fixes: the copy used to hardcode "AIR"
    # unconditionally, so a flash customer's failed payment produced an
    # email naming a plan and feature list ("managed AI audits, AIRview,
    # ... endpoint monitoring") they never had.
    plan_name = _plan_display_name(plan)
    features = "automated PR reviews" if plan == "flash" else (
        "managed AI audits, AIRview, automated PR reviews, and endpoint monitoring"
    )
    subject = f"Your {plan_name} payment failed"
    preheader = f"{account_login} is back on the free Community plan until you resubscribe."
    text = (
        "Hi,\n\n"
        f"A payment for {account_login}'s {plan_name} subscription didn't go "
        "through, and the account has moved back to the free Community plan.\n\n"
        f"Your scan history and settings are untouched - resubscribing restores "
        f"{plan_name} immediately: {features}.\n\n"
        "Resubscribe here: https://aletheore.com/pricing.html\n\n"
        "If this looks wrong, just reply to this email and we'll sort it out."
        f"{_FOOTER_TEXT}"
    )
    body_html = (
        '<p style="margin:0 0 16px;">Hi,</p>'
        f'<p style="margin:0 0 16px;">A payment for <strong>{account_login}</strong>'
        f'\'s {plan_name} subscription didn\'t go through, and the account has '
        'moved back to the free Community plan.</p>'
        f'<p style="margin:0 0 24px;">Your scan history and settings are '
        f'untouched &mdash; resubscribing restores {plan_name} immediately: '
        f'{features}.</p>'
        f'<p style="margin:0 0 24px;">{_button(f"Resubscribe to {plan_name}", _PRICING_URL)}</p>'
        f'<p style="margin:0;color:{_TEXT_MUTED};font-size:14px;">If this looks '
        'wrong, just reply to this email and we\'ll sort it out.</p>'
    )
    html = _shell(preheader, body_html)
    return {"subject": subject, "html": html, "text": text}


def deletion_otp_email(account_login: str, code: str) -> dict:
    subject = f"Your Aletheore deletion code: {code}"
    preheader = f"Confirm you want to permanently delete all data for {account_login}."
    text = (
        f"Someone requested deletion of all Aletheore data for {account_login}.\n\n"
        f"Confirmation code: {code}\n\n"
        "This code expires in 10 minutes and can be used once. If you didn't "
        "request this, ignore this email - nothing is deleted without it."
        f"{_FOOTER_TEXT}"
    )
    body_html = (
        f'<p style="margin:0 0 16px;">Someone requested deletion of all Aletheore '
        f'data for <strong>{account_login}</strong>.</p>'
        f'<p style="margin:0 0 8px;color:{_TEXT_MUTED};font-size:14px;">Confirmation code</p>'
        f'<p style="margin:0 0 24px;font-size:32px;font-weight:700;letter-spacing:4px;'
        f'font-family:{_FONT};">{code}</p>'
        f'<p style="margin:0;color:{_TEXT_MUTED};font-size:14px;">This code expires in 10 '
        'minutes and can be used once. If you didn\'t request this, ignore this email '
        '&mdash; nothing is deleted without it.</p>'
    )
    html = _shell(preheader, body_html)
    return {"subject": subject, "html": html, "text": text}


def _slack_markdown_to_html(text: str) -> str:
    # text is built from scan_worker/slack.py's format_* alert functions,
    # which interpolate repository-controlled content (commit subjects,
    # symbol names, risk summaries) - escape first, then promote markdown
    # on the escaped text, same ordering the wiki markdown renderer already
    # uses for the same reason (see test_wiki_markdown_escapes_before_
    # promoting_tags in test_frontend_js_syntax.py). Escaping first also
    # means the *bold*/`code` markers below still match literally, since
    # html.escape doesn't touch '*' or '`'.
    #
    # Only the two markdown patterns those builders actually produce
    # (*bold* and `code`) - not a general markdown parser, since there's
    # nothing else to convert.
    escaped = html.escape(text)
    converted = re.sub(r"`([^`]+)`", rf'<code style="background:{_BODY_BG};padding:2px 6px;border-radius:4px;">\1</code>', escaped)
    converted = re.sub(r"\*([^*]+)\*", r"<strong>\1</strong>", converted)
    return converted.replace("\n", "<br>")


def weekly_digest_email(
    account_login: str,
    plan: str,
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
    #
    # plan is the installation's CURRENT plan (unlike payment_failed_email/
    # subscription_canceled_email's previous_plan - this digest only fires
    # for a still-paying installation). Real bug this fixes: the copy used
    # to hardcode "Aletheore AIR" and unconditionally promote endpoint
    # monitoring + a managed dashboard - both AIR-exclusive - to every
    # paid installation including flash, which has neither.
    plan_name = _plan_display_name(plan)
    is_air = plan == "air"

    if scans_this_week > 0:
        scan_line = f"{scans_this_week} scan{'s' if scans_this_week != 1 else ''} run this week."
    else:
        scan_line = (
            "No scans run this week - push to a repo, or run "
            "`aletheore audit . --managed`, and it'll show up here next week."
        )

    if is_air:
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

    lines = [scan_line]
    if is_air:
        lines.append(health_line)
    lines.append(spend_line)

    cta_label = "Open your dashboard" if is_air else "Manage your subscription"
    cta_url = _DASHBOARD_URL if is_air else _PRICING_URL

    subject = f"Your {plan_name} weekly digest for {account_login}"
    preheader = f"{scan_line} {lines[1] if is_air else spend_line}"
    text = (
        "Hi,\n\n"
        f"Here's what happened on {account_login}'s {plan_name} this week:\n\n"
        + "".join(f"- {line}\n" for line in lines)
        + f"\n{cta_label}: {cta_url}\n\n"
        "Don't want these emails? Reply and let us know - we'll turn them off."
        f"{_FOOTER_TEXT}"
    )
    body_html = (
        '<p style="margin:0 0 16px;">Hi,</p>'
        f'<p style="margin:0 0 16px;">Here\'s what happened on '
        f'<strong>{account_login}</strong>\'s {plan_name} this week:</p>'
        f'<ul style="margin:0 0 24px;padding-left:20px;">'
        + "".join(
            f'<li style="margin-bottom:8px;">{line}</li>' if i < len(lines) - 1 else f'<li>{line}</li>'
            for i, line in enumerate(lines)
        )
        + '</ul>'
        f'<p style="margin:0 0 24px;">{_button(cta_label, cta_url)}</p>'
        f'<p style="margin:0;color:{_TEXT_MUTED};font-size:14px;">Don\'t want these '
        'emails? Reply and let us know &mdash; we\'ll turn them off.</p>'
    )
    html = _shell(preheader, body_html)
    return {"subject": subject, "html": html, "text": text}


def subscription_canceled_email(account_login: str, plan: str) -> dict:
    # plan is the PLAN BEING LOST (paddle.py's previous_plan), same
    # reasoning as payment_failed_email above - fixes the same hardcoded
    # "AIR" bug for a canceling flash customer.
    plan_name = _plan_display_name(plan)
    features = "automated PR reviews" if plan == "flash" else (
        "managed AI audits, AIRview architecture maps, AI-generated Docs, "
        "automated PR reviews, Slack/Teams alerts, and endpoint health monitoring"
    )
    subject = "Sorry to see you go"
    preheader = f"{account_login} is back on Community. Your history and settings are still here."
    text = (
        "Hi,\n\n"
        f"{account_login}'s {plan_name} subscription has been canceled and the "
        "account is back on the free Community plan. Your scan history and "
        "settings are still there if you come back.\n\n"
        f"Here's what's paused: {features}. The full deterministic CLI scanner "
        "stays free, forever, on Community.\n\n"
        "Resubscribe here: https://aletheore.com/pricing.html\n\n"
        "Canceled by mistake, or something didn't work the way you expected? "
        "Just reply - we'd genuinely like to know why."
        f"{_FOOTER_TEXT}"
    )
    body_html = (
        '<p style="margin:0 0 16px;">Hi,</p>'
        f'<p style="margin:0 0 16px;"><strong>{account_login}</strong>\'s {plan_name} '
        'subscription has been canceled and the account is back on the free '
        'Community plan. Your scan history and settings are still there if you '
        'come back.</p>'
        f'<p style="margin:0 0 24px;">Here\'s what\'s paused: {features}. The full '
        'deterministic CLI scanner stays free, forever, on Community.</p>'
        f'<p style="margin:0 0 24px;">{_button(f"Resubscribe to {plan_name}", _PRICING_URL)}</p>'
        f'<p style="margin:0;color:{_TEXT_MUTED};font-size:14px;">Canceled by '
        'mistake, or something didn\'t work the way you expected? Just reply '
        '&mdash; we\'d genuinely like to know why.</p>'
    )
    html = _shell(preheader, body_html)
    return {"subject": subject, "html": html, "text": text}


def health_alert_email(alert_text: str) -> dict:
    # alert_text is exactly what scan_worker/slack.py's format_reachability_alert/
    # format_latency_alert/format_shape_change_alert already built for
    # Slack/Teams - reused verbatim as the email's plain-text body (the
    # *bold*/`code` markdown reads fine unconverted as plain text) and
    # converted to HTML for the html body, rather than writing parallel
    # copy for a second channel.
    subject = "Aletheore endpoint alert"
    # Truncate/strip the raw text first, escape last - escaping expands
    # characters into multi-character entities, so escaping before
    # truncating risks slicing an entity in half.
    preheader = html.escape(re.sub(r"[`*]", "", alert_text.split("\n", 1)[0])[:140])
    text = f"{alert_text}{_FOOTER_TEXT}"
    body_html = f'<p style="margin:0;">{_slack_markdown_to_html(alert_text)}</p>'
    rendered_html = _shell(preheader, body_html)
    return {"subject": subject, "html": rendered_html, "text": text}
