import logging

from fastapi import APIRouter, Request, Response

from app_server.config import get_settings
from app_server.db import backfill_customer_email_for_claims, insert_pending_subscription_claim
from app_server.paddle_pricing import resolve_plan_for_price_id
from app_server.paddle_webhook_verify import verify_paddle_signature

paddle_webhook_router = APIRouter()
logger = logging.getLogger(__name__)


@paddle_webhook_router.post("/webhooks/paddle")
async def handle_paddle_webhook(request: Request) -> Response:
    raw_body = await request.body()
    signature = request.headers.get("paddle-signature", "")
    settings = get_settings()
    if not signature or not verify_paddle_signature(raw_body, signature, settings.paddle_webhook_secret):
        return Response(status_code=401)

    payload = await request.json()
    event_type = payload.get("event_type")
    data = payload.get("data") or {}
    if event_type == "subscription.created":
        claim_token = (data.get("custom_data") or {}).get("claim_token")
        items = data.get("items") or []
        price_id = items[0].get("price", {}).get("id") if items else None
        plan = resolve_plan_for_price_id(price_id) if price_id else None
        if claim_token and plan:
            await insert_pending_subscription_claim(
                request.app.state.db_pool,
                claim_token,
                data["id"],
                data["customer_id"],
                None,
                plan,
            )
        else:
            logger.warning("subscription.created missing claim token or known price id: price_id=%s", price_id)
    elif event_type in ("customer.created", "customer.updated"):
        email = data.get("email")
        if data.get("id") and email:
            await backfill_customer_email_for_claims(request.app.state.db_pool, data["id"], email)

    return Response(status_code=200)
