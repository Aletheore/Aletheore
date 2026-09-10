import logging
import math
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
    credit_extra_seat_purchase,
    credit_topup_purchase,
    get_extra_seats,
    get_installation,
    list_installation_member_emails,
    release_webhook_delivery,
    reset_billing_period_credit,
    set_extra_seats,
    set_installation_plan,
    set_paid_installation_plan,
)
from app_server.email_queue import enqueue_transactional_email
from app_server.error_alerts import send_error_alert
from app_server.paddle_ip_allowlist import client_ip_from_forwarded_for, is_known_paddle_ip
from app_server.paddle_pricing import (
    CREDIT_TOPUP_PRICE_ID,
    EXTRA_SEAT_PRICE_ID,
    PLAN_INTERVAL_TO_PRICE_ID,
    resolve_plan_for_price_id,
)
from app_server.paddle_webhook_verify import verify_paddle_signature

paddle_webhook_router = APIRouter()
logger = logging.getLogger(__name__)

# Real gap found and fixed 2026-09-02, before this had ever been exercised
# by a real customer: this handler code has always understood
# "adjustment.created" (reverses an affiliate's commission on a refund or
# chargeback, see _handle_adjustment_created), but the live Paddle
# notification destination (Paddle dashboard > Developer tools >
# Notifications > "Paddle Webhook", ntfset_01kyksktbmvr49pyygmxa3vfjz) was
# never actually subscribed to it - confirmed directly via the Paddle API,
# not assumed from this file's own code. Code handling an event Paddle
# never delivers is invisible: no error, no log line, nothing - the
# handler simply never runs. A real refund or chargeback would have left
# the referring affiliate's commission un-reversed indefinitely.
#
# The events this file's code paths handle: transaction.completed,
# adjustment.created, transaction.updated, and every name in
# _SUBSCRIPTION_EVENT_TYPES below. All of those except transaction.updated
# must always be a subset of the live destination's subscribed_events -
# transaction.updated is the deliberate exception: it's only checked for a
# refunded/partially_refunded/charged_back status, which Paddle's own
# adjustment.created already covers, so it's intentionally left
# unsubscribed live rather than added as a second path to the same
# reversal logic. Adding a new event_type branch to this file (other than
# that one deliberate exception) without also adding it to the live
# destination reproduces exactly the gap above - silently, with no test
# able to catch it, since nothing here can observe Paddle's own dashboard
# state.

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


def _seat_item_quantity(item: dict) -> int:
    """A line item's quantity, coerced to a real int - never trusts Paddle's
    own JSON shape to guarantee an int the way `item.get("quantity", 0)`
    implicitly did. That default only ever applies when the key is
    MISSING, not when Paddle sends it present with an explicit null or a
    numeric string - confirmed directly: quantity=None or quantity="3"
    both crashed the whole webhook handler with an unhandled TypeError
    on the sum() below, permanently stuck (Paddle keeps retrying the
    same payload) rather than a legitimate plan/seat change ever
    applying for that customer.
    """
    quantity = item.get("quantity")
    if isinstance(quantity, bool):
        return 0
    if isinstance(quantity, int):
        return quantity
    if isinstance(quantity, float):
        # Flash Review finding: JSON's own grammar has no literal for
        # inf/-inf/nan, but Python's json module accepts them anyway
        # (a real, if nonstandard, shape a sender can transmit) - int()
        # on either raises OverflowError (inf) or ValueError (nan), the
        # exact same "crash the whole webhook handler" failure mode this
        # function exists to close for None/string quantity.
        if not math.isfinite(quantity):
            return 0
        return int(quantity)
    if isinstance(quantity, str):
        try:
            return int(quantity)
        except ValueError:
            return 0
    return 0


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
        # either item once seats are involved. The matched price ID itself
        # is kept, not just the plan name it resolves to: a plan has both a
        # monthly and (for AIR) an annual price, and only the price ID says
        # which of the two this subscription is actually billed on - needed
        # for is_annual below.
        matched_price_id = next(
            (
                (item.get("price") or {}).get("id")
                for item in items
                if resolve_plan_for_price_id((item.get("price") or {}).get("id"))
            ),
            None,
        )
        plan = resolve_plan_for_price_id(matched_price_id) if matched_price_id else None
        if not plan:
            logger.warning(
                "%s has an active status but no resolvable plan price id in items", event_type
            )
            return
    else:
        plan = "free"
        matched_price_id = None

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
            _seat_item_quantity(item)
            for item in items
            if (item.get("price") or {}).get("id") == EXTRA_SEAT_PRICE_ID
        )
        if plan != "free"
        else 0
    )

    # A genuine billing-period renewal resets base_credit_remaining_usd to
    # the plan's real included credit (see db.py's reset_billing_period_
    # credit) - gated on plan != "free" the same way extra_seats above is,
    # since current_billing_period is only meaningful for an active paid
    # subscription: a cancellation or a past_due card decline already
    # resolves plan to "free" above and shouldn't reset anything. Also a
    # no-op (reset_billing_period_credit itself checks this) when
    # current_billing_period.starts_at hasn't actually changed - a replayed
    # or unrelated subscription.updated for the same period must not wipe
    # out credit the installation has already spent down. Folded into the
    # same transaction as the plan/extra_seats/Paddle-id writes below
    # (rather than run as its own standalone call first) so a crash between
    # this reset and that block can't leave base_credit_remaining_usd and
    # current_billing_period_start pointed at the new period while plan/
    # extra_seats/Paddle IDs stay stale - the same split-write hazard
    # documented on that block below, and reset_billing_period_credit only
    # ever calls .fetchrow() on what it's given, so passing it the open
    # `conn` from that transaction instead of `pool` works unchanged.
    period_start = (data.get("current_billing_period") or {}).get("starts_at")

    # An ANNUAL subscriber's current_billing_period.starts_at only advances
    # once a YEAR, so the reset above - the only thing that ever refreshes
    # base credit - would hand them 1/12th of the $18/month AIR allotment
    # that is meant to be monthly regardless of how the customer pays.
    # Flagging the annual price here is what lets reset_billing_period_
    # credit arm next_monthly_credit_reset_at, the synthetic monthly clock
    # scan_worker/jobs.py's run_monthly_credit_reset_sweep_job fires off.
    # Compared against the price ID rather than any interval field in the
    # payload: PLAN_INTERVAL_TO_PRICE_ID is this codebase's own source of
    # truth for which price means which interval (the same map the checkout
    # page builds from), so a plan with no annual price at all - "flash"
    # today - resolves to None and can never be mistaken for annual.
    is_annual = plan != "free" and matched_price_id == PLAN_INTERVAL_TO_PRICE_ID.get((plan, "year"))

    # One transaction, not three (now four) independent writes: a crash
    # between any two of these previously left the installation on the new
    # plan with stale extra_seats, or upgraded with no Paddle IDs recorded -
    # a state that persisted until a Paddle retry happened to land outside
    # claim_webhook_delivery's 15-minute reclaim window (see
    # docs/audits/Claude_Audit.md finding 11; confirmed live by injecting a
    # crash between add_paddle_ids_to_installation and set_extra_seats - the
    # plan and Paddle IDs committed, extra_seats never did). Rolling back
    # together means a crash here now looks identical to never having
    # started, from any later retry's point of view - no partial state to
    # reason about, whether the retry is immediate or 15 minutes later.
    #
    # Seats bought MID-CYCLE need their credit applied here, because the
    # reset above cannot do it: a seat purchase fires subscription.updated
    # with the SAME current_billing_period.starts_at, so
    # reset_billing_period_credit is a deliberate no-op and the per-seat
    # bonus baked into base_credit_for_plan never lands until the next real
    # renewal. Before this, a customer paid $6.99 for a seat and got $0 of
    # extra credit for up to a month (the old flat cap recomputed itself
    # live from get_extra_seats at every enforcement call site, so the
    # ceiling used to rise immediately). Read BEFORE set_extra_seats below
    # overwrites it, and applied inside the same transaction for the same
    # split-write reason documented on that block.
    previous_extra_seats = await get_extra_seats(pool, installation_id) if plan != "free" else 0

    transitioned_to_paid = False
    async with pool.acquire() as conn:
        async with conn.transaction():
            reset_happened = False
            if plan != "free" and period_start:
                reset_happened = await reset_billing_period_credit(
                    conn, installation_id, plan, extra_seats, period_start, is_annual
                )

            # Only when the renewal reset did NOT fire - a real reset already
            # sets the balance to base_credit_for_plan(plan, extra_seats),
            # which includes the new seat count, so crediting again on top of
            # it would double-count.
            if plan != "free" and not reset_happened and extra_seats > previous_extra_seats:
                await credit_extra_seat_purchase(
                    conn, installation_id, extra_seats - previous_extra_seats, plan, extra_seats
                )

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
    and only if the paying installation has a referral on file AND the
    transaction is not a credit top-up purchase (see the early return
    below - top-ups are pass-through LLM spend with no margin to pay a
    commission from). Every other (unreferred, or top-up) transaction.completed
    event - the overwhelming majority - is a fast no-op after the relevant
    check.

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

    # A customer-purchased credit top-up. This still needs to run before the
    # referral lookup below (rather than after an early return on "no
    # referral"), because a referred installation's top-up must still be
    # credited even though - see the early return at the end of this block -
    # it is deliberately excluded from earning its referrer any commission.
    items = data.get("items") or []
    topup_item = next(
        (item for item in items if (item.get("price") or {}).get("id") == CREDIT_TOPUP_PRICE_ID),
        None,
    )
    if topup_item is not None:
        # Credit the amount Paddle actually COLLECTED for this transaction,
        # not the line item's quantity - quantity assumes exactly $1 of
        # credit per unit and silently ignores any discount. A top-up
        # transaction never bundles a top-up with any other line item (see
        # the buyCredit() comment below), so details.totals.total - Paddle's
        # collected amount net of discount, in the currency's minor unit, as
        # a string - IS the real dollar amount collected for this top-up.
        # Same parsing pattern as the referral commission calculation below.
        transaction_id = data.get("id")
        total_raw = ((data.get("details") or {}).get("totals") or {}).get("total")
        if transaction_id and total_raw is not None:
            try:
                total_minor_units = Decimal(str(total_raw))
            except InvalidOperation:
                logger.warning(
                    "credit topup transaction.completed has an unparseable total: %s",
                    data.get("id"),
                )
            else:
                amount_usd = (total_minor_units / Decimal(100)).quantize(
                    Decimal("0.01"), rounding=ROUND_HALF_UP
                )
                await credit_topup_purchase(pool, installation_id, float(amount_usd), transaction_id)
        else:
            logger.warning(
                "credit topup transaction.completed missing total or id: %s",
                data.get("id"),
            )
        # Credit top-ups are pass-through LLM spend with near-zero margin -
        # paying 15% affiliate commission on them (as the code below would,
        # unconditionally, on the full transaction total) is a real loss with
        # no offsetting revenue to pay it from, unlike commission on a genuine
        # subscription/seat sale. Deliberately conservative: skip commission
        # for the WHOLE transaction if it contains a top-up item at all,
        # rather than trying to parse Paddle's per-line-item totals to
        # subtract just the top-up portion (this codebase has never parsed
        # per-item totals, only the transaction-level total) - the current
        # buyCredit() checkout flow is a standalone purchase action that
        # never bundles a top-up with a subscription/seat item in the same
        # transaction, so this has no practical downside today.
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
