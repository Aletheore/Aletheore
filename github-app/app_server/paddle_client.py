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
