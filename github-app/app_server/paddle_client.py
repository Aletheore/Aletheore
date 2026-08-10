import httpx

_PADDLE_API_BASE = "https://api.paddle.com"


class PaddleAPIError(Exception):
    pass


class PaddleAPINotConfigured(PaddleAPIError):
    pass


def _headers(api_key: str) -> dict:
    return {"Authorization": f"Bearer {api_key}"}


def get_subscription(api_key: str | None, subscription_id: str) -> dict:
    if not api_key:
        raise PaddleAPINotConfigured("PADDLE_API_KEY is not configured")
    try:
        response = httpx.get(f"{_PADDLE_API_BASE}/subscriptions/{subscription_id}", headers=_headers(api_key))
        response.raise_for_status()
    except httpx.HTTPError as exc:
        raise PaddleAPIError(f"could not fetch subscription {subscription_id}: {exc}") from exc
    return response.json()["data"]


def create_portal_session(
    api_key: str | None,
    customer_id: str,
    subscription_ids: list[str] | None = None,
) -> dict:
    """Paddle-hosted, temporary, authenticated link where a customer can
    update their payment method, view invoices, and manage or cancel a
    subscription - the self-serve fix for exactly what a failed-payment
    customer needs, without Aletheore building its own billing screens.
    Session URLs shouldn't be cached (Paddle's own guidance) - callers must
    request a fresh one per visit, not store the URL."""
    if not api_key:
        raise PaddleAPINotConfigured("PADDLE_API_KEY is not configured")
    payload = {"subscription_ids": subscription_ids} if subscription_ids else {}
    try:
        response = httpx.post(
            f"{_PADDLE_API_BASE}/customers/{customer_id}/portal-sessions",
            headers=_headers(api_key),
            json=payload,
        )
        response.raise_for_status()
    except httpx.HTTPError as exc:
        raise PaddleAPIError(f"could not create portal session for customer {customer_id}: {exc}") from exc
    return response.json()["data"]


def create_discount(api_key: str | None, code: str, description: str) -> dict:
    """Creates a merchant-defined percentage discount code in Paddle for an
    affiliate: 10% off, applied to exactly one billing period
    (maximum_recurring_intervals=1), enabled for checkout entry. The
    returned discount's id (dsc_...) is the attribution key stored against
    the affiliates row - see app_server/affiliates.py."""
    if not api_key:
        raise PaddleAPINotConfigured("PADDLE_API_KEY is not configured")
    payload = {
        "description": description,
        "type": "percentage",
        "amount": "10",
        "code": code,
        "recur": True,
        "maximum_recurring_intervals": 1,
        "enabled_for_checkout": True,
    }
    try:
        response = httpx.post(f"{_PADDLE_API_BASE}/discounts", headers=_headers(api_key), json=payload)
        response.raise_for_status()
    except httpx.HTTPError as exc:
        raise PaddleAPIError(f"could not create discount {code}: {exc}") from exc
    return response.json()["data"]


def update_subscription_items(
    api_key: str | None,
    subscription_id: str,
    items: list[dict],
    proration_billing_mode: str,
) -> dict:
    if not api_key:
        raise PaddleAPINotConfigured("PADDLE_API_KEY is not configured")
    # Paddle requires the COMPLETE item list on every update, not just the
    # items being changed - omitting an existing item removes it from the
    # subscription. Callers must fetch the current subscription first and
    # build the full list; this function does not merge for them.
    try:
        response = httpx.patch(
            f"{_PADDLE_API_BASE}/subscriptions/{subscription_id}",
            headers=_headers(api_key),
            json={"items": items, "proration_billing_mode": proration_billing_mode},
        )
        response.raise_for_status()
    except httpx.HTTPError as exc:
        raise PaddleAPIError(f"could not update subscription {subscription_id}: {exc}") from exc
    return response.json()["data"]
