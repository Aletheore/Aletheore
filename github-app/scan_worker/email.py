import httpx

RESEND_API_URL = "https://api.resend.com/emails"


def send_transactional_email(
    api_key: str,
    from_address: str,
    reply_to: str,
    to: str,
    subject: str,
    html: str,
    text: str,
    http_client: httpx.Client | None = None,
) -> dict:
    """Sends one email via Resend's API. Returns the parsed JSON response
    (contains "id", Resend's message id, stored on sent_emails for future
    delivery-status correlation). Raises on any non-2xx, same as
    send_health_alert/send_slack_alert - the caller (the RQ job) is what
    decides retry/dedupe behavior, not this function.
    """
    client = http_client or httpx.Client()
    response = client.post(
        RESEND_API_URL,
        headers={"Authorization": f"Bearer {api_key}"},
        json={
            "from": from_address,
            "to": [to],
            "reply_to": reply_to,
            "subject": subject,
            "html": html,
            "text": text,
        },
    )
    response.raise_for_status()
    return response.json()
