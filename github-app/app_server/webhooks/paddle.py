import logging
from datetime import datetime
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation

from fastapi import APIRouter, Request, Response

from app_server.affiliates import get_affiliate_by_discount_id, get_referral, record_commission, record_referral
from app_server.config import get_settings
from app_server.db import (
    add_paddle_ids_to_installation,
    claim_webhook_delivery,
    get_installation,
    list_installation_member_emails,
    release_webhook_delivery,
    set_extra_seats,
    set_installation_plan,
)
from app_server.email_queue import enqueue_transactional_email
from app_server.paddle_ip_allowlist import client_ip_from_forwarded_for, is_known_paddle_ip
from app_server.paddle_pricing import EXTRA_SEAT_PRICE_ID, resolve_plan_for_price_id
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

# The affiliate's payout share of what Paddle actually collects per
# transaction (net of that transaction's own discount) - see
# docs/superpowers/specs/2026-08-10-aletheore-affiliate-program-design.md.
_AFFILIATE_COMMISSION_RATE = Decimal("0.15")


async def handle_paddle_webhook_event(payload: dict, pool, redis_url: str, queue=None) -> None:
    event_type = payload.get("event_type")
    if event_type == "transaction.completed":
        await _handle_transaction_completed(payload.get("data") or {}, pool)
        return
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

    items = data.get("items") or []
    if data.get("status") in _ACTIVE_SUBSCRIPTION_STATUSES:
        # The base plan price is whichever item resolves to a known plan -
        # not necessarily items[0], since the extra-seat add-on can be
        # either item once seats are involved.
        plan = next(
            (
                resolved
                for item in items
                if (resolved := resolve_plan_for_price_id((item.get("price") or {}).get("id")))
            ),
            None,
        )
        if not plan:
            logger.warning(
                "%s has an active status but no resolvable plan price id in items", event_type
            )
            return
    else:
        plan = "free"

    previous = await get_installation(pool, installation_id)
    previous_plan = previous["plan"] if previous is not None else "free"

    await set_installation_plan(pool, installation_id, plan)
    if "id" in data and "customer_id" in data:
        await add_paddle_ids_to_installation(pool, installation_id, data["id"], data["customer_id"])

    # The extra-seat line item's quantity is the source of truth for billed
    # seats - reconciled here, the same way the plan itself is, rather than
    # trusting the buy/remove-seat button's own optimism about what Paddle
    # actually charged.
    extra_seats = (
        sum(
            item.get("quantity", 0)
            for item in items
            if (item.get("price") or {}).get("id") == EXTRA_SEAT_PRICE_ID
        )
        if plan != "free"
        else 0
    )
    await set_extra_seats(pool, installation_id, extra_seats)

    # One-time Live Wiki build, mirroring the GitHub Marketplace path in
    # webhooks/marketplace.py - fires exactly once, on the free -> paid
    # transition. Without this, installations upgraded through Paddle (the
    # actual live payment path) never get an initial AIRview build at all:
    # only the Marketplace webhook used to trigger it, so a Paddle
    # installation's wiki stayed limited to whatever clusters an
    # incremental push happened to touch after the fact.
    if previous_plan == "free" and plan != "free":
        # Attribution: first time this installation goes free -> paid, if it
        # was checked out with a known affiliate's discount code, credit
        # that affiliate. Gated the same free -> paid transition as the
        # wiki/docs build below, so a later subscription.updated for the
        # same installation (e.g. switching monthly <-> annual) can't
        # re-attribute or steal credit - record_referral is also itself a
        # database-enforced no-op past the first row (installation_id is
        # that table's primary key).
        discount_id = (data.get("discount") or {}).get("id")
        if discount_id:
            affiliate = await get_affiliate_by_discount_id(pool, discount_id)
            if affiliate is not None:
                await record_referral(pool, installation_id, affiliate["id"])

        if queue is None:
            from redis import Redis
            from rq import Queue

            queue = Queue("scans", connection=Redis.from_url(redis_url))
        queue.enqueue(
            "scan_worker.jobs.run_live_wiki_full_build_for_installation_job",
            job_timeout=60,
            installation_id=installation_id,
        )
        # One-time Docs build, same trigger and tier-independence as the
        # Live Wiki build immediately above.
        queue.enqueue(
            "scan_worker.jobs.run_live_docs_full_build_for_installation_job",
            job_timeout=60,
            installation_id=installation_id,
        )

    # payment_failed and subscription_canceled emails, gated on an actual
    # paid -> free transition (not "was already free") so a webhook for an
    # installation that was never paid, or one already downgraded by a
    # prior event, doesn't send anything. Distinguished by event_type +
    # status since both land here as plan == "free": a card decline
    # (subscription.updated, status=past_due - no dunning-aware grace
    # period exists, see _ACTIVE_SUBSCRIPTION_STATUSES above, so access is
    # already fully revoked by the time this fires) gets different copy
    # from an actual cancellation (subscription.canceled).
    if previous_plan != "free" and plan == "free":
        event_id = payload.get("event_id")
        template_name = None
        if event_type == "subscription.updated" and data.get("status") == "past_due":
            template_name = "payment_failed"
        elif event_type == "subscription.canceled":
            template_name = "subscription_canceled"

        if template_name and event_id:
            account_login = previous["account_login"] if previous is not None else str(installation_id)
            for member_email in await list_installation_member_emails(pool, installation_id):
                enqueue_transactional_email(
                    redis_url,
                    dedupe_key=f"{template_name}:{event_id}:{member_email}",
                    template_name=template_name,
                    template_arg=account_login,
                    to_email=member_email,
                    installation_id=installation_id,
                )


async def _handle_transaction_completed(data: dict, pool) -> None:
    """Records an affiliate commission for one completed transaction, if
    and only if the paying installation has a referral on file. Every other
    (unreferred) transaction.completed event - the overwhelming majority -
    is a fast no-op after the referral lookup.

    15% of `details.totals.total`, Paddle's collected amount net of that
    transaction's own discount, in the currency's minor unit (cents) as a
    string - matches the "15% of everything Paddle actually collects"
    scope decision for both a discounted first month and every undiscounted
    month after it, without special-casing either.
    """
    installation_id_raw = (data.get("custom_data") or {}).get("installation_id")
    installation_id = None
    if installation_id_raw is not None:
        try:
            installation_id = int(installation_id_raw)
        except (TypeError, ValueError):
            installation_id = None
    if installation_id is None:
        return

    referral = await get_referral(pool, installation_id)
    if referral is None:
        return

    transaction_id = data.get("id")
    total_raw = ((data.get("details") or {}).get("totals") or {}).get("total")
    billed_at_raw = data.get("billed_at") or data.get("created_at")
    if not transaction_id or total_raw is None or not billed_at_raw:
        logger.warning(
            "transaction.completed for a referred installation is missing fields "
            "needed for commission calculation"
        )
        return

    try:
        total_minor_units = Decimal(str(total_raw))
        billed_at = datetime.fromisoformat(billed_at_raw)
    except (InvalidOperation, ValueError):
        logger.warning("transaction.completed has an unparseable total or billed_at")
        return

    commission_usd = (total_minor_units / Decimal(100) * _AFFILIATE_COMMISSION_RATE).quantize(
        Decimal("0.01"), rounding=ROUND_HALF_UP
    )

    await record_commission(
        pool,
        referral["affiliate_id"],
        installation_id,
        transaction_id,
        commission_usd,
        billed_at,
    )


@paddle_webhook_router.post("/webhooks/paddle")
async def handle_paddle_webhook(request: Request) -> Response:
    raw_body = await request.body()
    signature = request.headers.get("paddle-signature", "")
    settings = get_settings()
    if not signature or not verify_paddle_signature(raw_body, signature, settings.paddle_webhook_secret):
        # Previously silent - a real signature failure (rotated secret,
        # clock drift past tolerance, a genuinely forged request) and a
        # missing header looked identical from the outside with nothing to
        # go on. header_present/header_len are safe to log (no secret or
        # payment data); the raw signature and body are not logged.
        logger.warning(
            "paddle webhook signature verification failed (header_present=%s, header_len=%d)",
            bool(signature),
            len(signature),
        )
        return Response(status_code=401)

    # Defense-in-depth on top of signature verification, not a replacement
    # for it: reject only when the source IP is definitively not one of
    # Paddle's published addresses. A fetch failure returns None (can't
    # verify) rather than False, so a transient outage reaching Paddle's own
    # /ips endpoint can't turn into rejecting every real webhook.
    client_ip = client_ip_from_forwarded_for(
        request.headers.get("x-forwarded-for"),
        request.client.host if request.client else "",
    )
    if await is_known_paddle_ip(client_ip) is False:
        logger.warning("rejected webhook from non-Paddle IP %s despite a valid signature", client_ip)
        return Response(status_code=401)

    try:
        payload = await request.json()
    except ValueError:
        return Response(status_code=401)
    if not isinstance(payload, dict):
        return Response(status_code=401)

    # Claimed after signature and IP verification, so an unauthenticated
    # caller can't burn an event id and suppress the genuine delivery.
    #
    # The signature's own 5s timestamp tolerance already makes captured
    # payload replay a narrow window. This is here for concurrency:
    # handle_paddle_webhook_event reads installations.plan, then writes it,
    # and gates a pair of expensive full AIRview/Docs builds on that read
    # having been "free". Two deliveries of the same event arriving together
    # both read "free" and both enqueue those builds - real duplicated LLM
    # spend. The claim is what makes that gate hold under concurrency.
    event_id = payload.get("event_id")
    if not isinstance(event_id, str) or not event_id:
        logger.warning("paddle webhook missing event_id, refusing to process undedupable event")
        return Response(status_code=400)

    pool = request.app.state.db_pool
    if not await claim_webhook_delivery(pool, "paddle", event_id, payload.get("event_type") or ""):
        logger.info("duplicate paddle webhook %s ignored", event_id)
        return Response(status_code=200)

    try:
        await handle_paddle_webhook_event(payload, pool, settings.redis_url)
    except Exception:
        # Hand the id back before failing, or Paddle's retry of this same
        # event would be discarded as a duplicate and the plan change lost.
        await release_webhook_delivery(pool, "paddle", event_id)
        raise

    return Response(status_code=200)
