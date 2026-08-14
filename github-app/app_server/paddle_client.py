import httpx

from app_server.http_client import get_generic_http_client

_PADDLE_API_BASE = "https://api.paddle.com"
_PADDLE_TIMEOUT_SECONDS = 15.0


class PaddleAPIError(Exception):
    pass


class PaddleAPINotConfigured(PaddleAPIError):
    pass


def _headers(api_key: str) -> dict:
    return {"Authorization": f"Bearer {api_key}"}


def _data_or_raise(response: httpx.Response, context: str) -> dict:
    try:
        return response.json()["data"]
    except (KeyError, TypeError, ValueError) as exc:
        raise PaddleAPIError(f"{context}: unexpected Paddle response shape") from exc


def get_subscription(api_key: str | None, subscription_id: str) -> dict:
    if not api_key:
        raise PaddleAPINotConfigured("PADDLE_API_KEY is not configured")
    try:
        response = get_generic_http_client().get(
            f"{_PADDLE_API_BASE}/subscriptions/{subscription_id}",
            headers=_headers(api_key),
            timeout=_PADDLE_TIMEOUT_SECONDS,
        )
        response.raise_for_status()
    except httpx.HTTPError as exc:
        raise PaddleAPIError(f"could not fetch subscription {subscription_id}: {exc}") from exc
    return _data_or_raise(response, f"could not fetch subscription {subscription_id}")


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
        response = get_generic_http_client().post(
            f"{_PADDLE_API_BASE}/customers/{customer_id}/portal-sessions",
            headers=_headers(api_key),
            json=payload,
            timeout=_PADDLE_TIMEOUT_SECONDS,
        )
        response.raise_for_status()
    except httpx.HTTPError as exc:
        raise PaddleAPIError(f"could not create portal session for customer {customer_id}: {exc}") from exc
    return _data_or_raise(response, f"could not create portal session for customer {customer_id}")


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
        response = get_generic_http_client().post(
            f"{_PADDLE_API_BASE}/discounts",
            headers=_headers(api_key),
            json=payload,
            timeout=_PADDLE_TIMEOUT_SECONDS,
        )
        response.raise_for_status()
    except httpx.HTTPError as exc:
        raise PaddleAPIError(f"could not create discount {code}: {exc}") from exc
    return _data_or_raise(response, f"could not create discount {code}")


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
        response = get_generic_http_client().patch(
            f"{_PADDLE_API_BASE}/subscriptions/{subscription_id}",
            headers=_headers(api_key),
            json={"items": items, "proration_billing_mode": proration_billing_mode},
            timeout=_PADDLE_TIMEOUT_SECONDS,
        )
        response.raise_for_status()
    except httpx.HTTPError as exc:
        raise PaddleAPIError(f"could not update subscription {subscription_id}: {exc}") from exc
    return _data_or_raise(response, f"could not update subscription {subscription_id}")
