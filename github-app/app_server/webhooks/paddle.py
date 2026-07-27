import logging

from fastapi import APIRouter, Request, Response

from app_server.config import get_settings
from app_server.db import add_paddle_ids_to_installation, get_installation, set_installation_plan
from app_server.paddle_pricing import resolve_plan_for_price_id
from app_server.paddle_webhook_verify import verify_paddle_signature

paddle_webhook_router = APIRouter()
logger = logging.getLogger(__name__)

# Every subscription lifecycle event that can change what plan an
# installation should be on. Previously only subscription.created was
# handled - a cancellation, a card declining until the subscription lapsed,
# or a tier change via Paddle's own customer portal all landed as one of
# these other event types and were silently dropped, leaving
# installations.plan stuck at whatever it was last set to indefinitely.
_SUBSCRIPTION_EVENT_TYPES = {
    "subscription.created",
    "subscription.updated",
    "subscription.canceled",
    "subscription.paused",
    "subscription.resumed",
}

# Paddle subscription statuses that keep (or restore) paid access. Anything
# else - canceled, paused, past_due, or any future status - revokes to
# free immediately. There's no dunning-aware "past due but still allowed"
# grace tier in this product; erring toward cutting access rather than
# silently extending it is the safer default for a paid feature.
_ACTIVE_SUBSCRIPTION_STATUSES = {"active", "trialing"}


async def handle_paddle_webhook_event(payload: dict, pool, redis_url: str, queue=None) -> None:
    event_type = payload.get("event_type")
    if event_type not in _SUBSCRIPTION_EVENT_TYPES:
        return

    data = payload.get("data") or {}
    installation_id_raw = (data.get("custom_data") or {}).get("installation_id")
    installation_id = None
    if installation_id_raw is not None:
        try:
            installation_id = int(installation_id_raw)
        except (TypeError, ValueError):
            installation_id = None
    if installation_id is None:
        logger.warning("%s missing installation_id in custom_data", event_type)
        return

    if data.get("status") in _ACTIVE_SUBSCRIPTION_STATUSES:
        items = data.get("items") or []
        price_id = items[0].get("price", {}).get("id") if items else None
        plan = resolve_plan_for_price_id(price_id) if price_id else None
        if not plan:
            logger.warning(
                "%s has an active status but no resolvable price id: price_id=%s", event_type, price_id
            )
            return
    else:
        plan = "free"

    previous = await get_installation(pool, installation_id)
    previous_plan = previous["plan"] if previous is not None else "free"

    await set_installation_plan(pool, installation_id, plan)
    if "id" in data and "customer_id" in data:
        await add_paddle_ids_to_installation(pool, installation_id, data["id"], data["customer_id"])

    # One-time Live Wiki build, mirroring the GitHub Marketplace path in
    # webhooks/marketplace.py - fires exactly once, on the free -> paid
    # transition. Without this, installations upgraded through Paddle (the
    # actual live payment path) never get an initial AIRview build at all:
    # only the Marketplace webhook used to trigger it, so a Paddle
    # installation's wiki stayed limited to whatever clusters an
    # incremental push happened to touch after the fact.
    if previous_plan == "free" and plan != "free":
        if queue is None:
            from redis import Redis
            from rq import Queue

            queue = Queue("scans", connection=Redis.from_url(redis_url))
        queue.enqueue(
            "scan_worker.jobs.run_live_wiki_full_build_for_installation_job",
            job_timeout=60,
            installation_id=installation_id,
        )


@paddle_webhook_router.post("/webhooks/paddle")
async def handle_paddle_webhook(request: Request) -> Response:
    raw_body = await request.body()
    signature = request.headers.get("paddle-signature", "")
    settings = get_settings()
    if not signature or not verify_paddle_signature(raw_body, signature, settings.paddle_webhook_secret):
        return Response(status_code=401)

    payload = await request.json()
    await handle_paddle_webhook_event(payload, request.app.state.db_pool, settings.redis_url)

    return Response(status_code=200)
