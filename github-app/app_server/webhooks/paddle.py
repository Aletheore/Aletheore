import logging

from fastapi import APIRouter, Request, Response

from app_server.config import get_settings
from app_server.db import add_paddle_ids_to_installation, set_installation_plan
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
        installation_id_raw = (data.get("custom_data") or {}).get("installation_id")
        items = data.get("items") or []
        price_id = items[0].get("price", {}).get("id") if items else None
        plan = resolve_plan_for_price_id(price_id) if price_id else None

        installation_id = None
        if installation_id_raw is not None:
            try:
                installation_id = int(installation_id_raw)
            except (TypeError, ValueError):
                installation_id = None

        if installation_id is not None and plan:
            pool = request.app.state.db_pool
            await set_installation_plan(pool, installation_id, plan)
            await add_paddle_ids_to_installation(pool, installation_id, data["id"], data["customer_id"])
        else:
            logger.warning(
                "subscription.created missing installation_id or known price id: price_id=%s", price_id
            )

    return Response(status_code=200)
