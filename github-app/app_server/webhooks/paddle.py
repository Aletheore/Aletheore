import logging
from datetime import datetime
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation

from fastapi import APIRouter, Request, Response

from app_server.affiliates import get_affiliate_by_discount_id, get_referral, record_commission, record_referral
from app_server.auth import unsign_checkout_installation_id
from app_server.config import get_settings
from app_server.db import (
    add_paddle_ids_to_installation,
    claim_webhook_delivery,
    claim_free_to_paid_plan,
    claim_paid_setup,
    get_installation,
    list_installation_member_emails,
    release_webhook_delivery,
    set_extra_seats,
    set_installation_plan,
    set_paid_installation_plan,
)
from app_server.email_queue import enqueue_transactional_email
from app_server.error_alerts import send_error_alert
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


class PaddleWebhookAttributionError(RuntimeError):
    """A real, signature-verified Paddle webhook that can't be attributed
    to any installation - see the installation_id is None branch below."""


async def handle_paddle_webhook_event(payload: dict, pool, redis_url: str, queue=None) -> None:
    event_type = payload.get("event_type")
    if event_type == "transaction.completed":
        await _handle_transaction_completed(payload.get("data") or {}, pool)
        return
    if event_type == "adjustment.created" or (
        event_type == "transaction.updated"
        and (payload.get("data") or {}).get("status")
        in {"refunded", "partially_refunded", "charged_back"}
    ):
        await _handle_adjustment_created(payload.get("data") or {}, pool)
        return
    if event_type not in _SUBSCRIPTION_EVENT_TYPES:
        return

    data = payload.get("data") or {}
    # Signed, not a raw integer: custom_data is set by the browser calling
    # Paddle.Checkout.open(), which nothing stops from being called directly
    # with any custom_data - the Paddle signature on this webhook proves
    # only "Paddle sent this event", never "the payer was authorized to
    # name this installation". unsign_checkout_installation_id verifies the
    # token was minted server-side, for this exact installation, to a
    # session that was already checked against
    # _administered_installation_ids_for_session_or_401 - see
    # sign_checkout_installation_id and frontend.py's checkout page.
    installation_token = (data.get("custom_data") or {}).get("installation_token")
    installation_id = (
        unsign_checkout_installation_id(installation_token, get_settings().session_secret)
        if installation_token
        else None
    )
    if installation_id is None:
        # A real, signature-verified Paddle event (not a spoofed/tampered
        # token - those are legitimately silent, see the tests covering
        # them above) whose plan-flip can't be attributed to anyone. Still
        # returns 200 below (no reason to make Paddle retry a payload that
        # will never carry a valid token no matter how many times it's
        # redelivered), so nothing else would ever surface this - a real
        # payer's subscription silently not activating would otherwise look
        # identical to a successful transaction from the outside.
        logger.warning("%s missing or invalid installation_token in custom_data", event_type)
        send_error_alert(
            "paddle_webhook",
            PaddleWebhookAttributionError(f"{event_type} missing or invalid installation_token"),
            f"event_id={payload.get('event_id')} subscription_id={data.get('id')}",
        )
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

    # Defense in depth beyond the signed token above: once an installation
    # has a Paddle customer on file, only that same customer's events may
    # mutate it. Never blocks the first subscription.created for a fresh
    # installation (nothing stored yet to mismatch against), but closes the
    # billing-portal-hijack path even if a future change ever reintroduced
    # a spoofable identifier into custom_data.
    previous_customer_id = previous.get("paddle_customer_id") if previous is not None else None
    event_customer_id = data.get("customer_id")
    if previous_customer_id and event_customer_id and previous_customer_id != event_customer_id:
        logger.warning(
            "%s customer_id mismatch for installation=%s - ignoring",
            event_type,
            installation_id,
        )
        return

    # The extra-seat line item's quantity is the source of truth for billed
    # seats - reconciled here, the same way the plan itself is, rather than
    # trusting the buy/remove-seat button's own optimism about what Paddle
    # actually charged. Computed before the transaction below since it only
    # reads `items`/`plan`, already available.
    extra_seats = (
        sum(
            item.get("quantity", 0)
            for item in items
            if (item.get("price") or {}).get("id") == EXTRA_SEAT_PRICE_ID
        )
        if plan != "free"
        else 0
    )

    # One transaction, not three independent writes: a crash between any two
    # of these previously left the installation on the new plan with stale
    # extra_seats, or upgraded with no Paddle IDs recorded - a state that
    # persisted until a Paddle retry happened to land outside
    # claim_webhook_delivery's 15-minute reclaim window (see
    # docs/audits/Claude_Audit.md finding 11; confirmed live by injecting a
    # crash between add_paddle_ids_to_installation and set_extra_seats - the
    # plan and Paddle IDs committed, extra_seats never did). Rolling back
    # together means a crash here now looks identical to never having
    # started, from any later retry's point of view - no partial state to
    # reason about, whether the retry is immediate or 15 minutes later.
    transitioned_to_paid = False
    async with pool.acquire() as conn:
        async with conn.transaction():
            if plan != "free":
                transitioned_to_paid = await claim_free_to_paid_plan(conn, installation_id, plan)
                if not transitioned_to_paid:
                    await set_paid_installation_plan(conn, installation_id, plan)
            else:
                await set_installation_plan(conn, installation_id, plan)

            if "id" in data and "customer_id" in data:
                await add_paddle_ids_to_installation(conn, installation_id, data["id"], data["customer_id"])

            await set_extra_seats(conn, installation_id, extra_seats)

    # Deliberately independent of transitioned_to_paid, and deliberately
    # outside the transaction above: if a crash lands between that
    # transaction committing and the one-time setup below actually running,
    # a Paddle retry finds plan already non-free, so claim_free_to_paid_plan
    # correctly returns False on the retry - but setup still never ran once.
    # This claim is what actually decides whether to run it, so a
    # crash-then-retry still runs it exactly once instead of silently
    # skipping it forever.
    should_run_paid_setup = plan != "free" and await claim_paid_setup(pool, installation_id)

    if should_run_paid_setup:
        # Attribution: first time this installation goes free -> paid, on
        # ANY paid plan (flash included) - if it was checked out with a
        # known affiliate's discount code, credit that affiliate. Gated on
        # the same paid-setup claim as the AIRview/Docs build below, so a
        # later subscription.updated for the same installation (e.g.
        # switching monthly <-> annual) can't re-attribute or steal credit
        # - record_referral is also itself a database-enforced no-op past
        # the first row (installation_id is that table's primary key).
        discount_id = (data.get("discount") or {}).get("id")
        if discount_id:
            affiliate = await get_affiliate_by_discount_id(pool, discount_id)
            if affiliate is not None:
                await record_referral(pool, installation_id, affiliate["id"])

        # One-time Live Wiki + Docs build, mirroring the GitHub Marketplace
        # path in webhooks/marketplace.py - fires exactly once, on the
        # free -> paid transition. Without this, installations upgraded
        # through Paddle (the actual live payment path) never get an
        # initial AIRview build at all: only the Marketplace webhook used
        # to trigger it, so a Paddle installation's wiki stayed limited to
        # whatever clusters an incremental push happened to touch after
        # the fact.
        #
        # AIR-exclusive (plan == "air"), unlike the affiliate credit above
        # - AIRview and Docs are not part of the flash tier. Real bug this
        # closes: should_run_paid_setup predates the flash tier and used
        # to mean "is air" by construction (air was the only paid plan),
        # so a flash signup would have silently kicked off a full
        # AIRview + Docs build - real LLM spend against a $6/mo plan whose
        # own $4 cap override (llm_cost.py's PLAN_CAP_OVERRIDE_USD) a
        # single full build could plausibly exhaust before the customer's
        # first PR review ever ran.
        #
        # A flash -> air upgrade doesn't get this instant build (this
        # claim was already consumed on that installation's original
        # free -> flash transition), but self-heals within one scheduler
        # tick: scan_worker/db.py's list_paid_repos_due_for_wiki_catchup/
        # list_paid_repos_due_for_docs_catchup are also AIR-exclusive, so
        # an installation that was never eligible while on flash has no
        # wiki_catchup_sweeps/docs_catchup_sweeps row yet - the moment it
        # becomes "air", the sweep's own "never swept" branch picks it up
        # without needing any special-cased upgrade handling here.
        if plan == "air":
            if queue is None:
                from redis import Redis
                from rq import Queue

                queue = Queue("scans", connection=Redis.from_url(redis_url))
            queue.enqueue(
                "scan_worker.jobs.run_live_wiki_full_build_for_installation_job",
                job_timeout=60,
                installation_id=installation_id,
            )
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
            # The plan being LOST, not the current (now "free") plan - both
            # templates need it to name what's actually being paused (real
            # bug this fixes: the copy used to hardcode "AIR" regardless of
            # whether the installation was actually air or flash).
            for member_email in await list_installation_member_emails(pool, installation_id):
                enqueue_transactional_email(
                    redis_url,
                    dedupe_key=f"{template_name}:{event_id}:{member_email}",
                    template_name=template_name,
                    template_arg={"account_login": account_login, "plan": previous_plan},
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

    installation_id comes from the same signed custom_data.installation_token
    as the subscription handlers above, for the same reason: an unsigned,
    caller-supplied installation_id here would let anyone checking out for
    themselves name a different, referred installation and misattribute the
    resulting commission to that installation's affiliate.
    """
    installation_token = (data.get("custom_data") or {}).get("installation_token")
    installation_id = (
        unsign_checkout_installation_id(installation_token, get_settings().session_secret)
        if installation_token
        else None
    )
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


async def _handle_adjustment_created(data: dict, pool) -> None:
    """Reverse a commission when Paddle refunds or charges back a transaction."""
    transaction_id = (
        data.get("transaction_id")
        or data.get("id")
        or (data.get("transaction") or {}).get("id")
    )
    if transaction_id:
        from app_server.affiliates import reverse_commission

        await reverse_commission(pool, transaction_id)


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
    # The signature's own 60s timestamp tolerance already makes captured
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
