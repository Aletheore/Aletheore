import asyncio
import logging
import math
from datetime import datetime
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation

from fastapi import APIRouter, Request, Response

from app_server.affiliates import get_affiliate_by_discount_id, get_referral, record_commission, record_referral
from app_server.auth import CHECKOUT_TOKEN_WEBHOOK_MAX_AGE, unsign_checkout_installation_id
from app_server.config import get_settings
from app_server.db import (
    add_paddle_ids_to_installation,
    claim_webhook_delivery,
    claim_free_to_paid_plan,
    claim_paid_setup,
    release_paid_setup,
    credit_extra_seat_purchase,
    claw_back_topup_credit,
    credit_topup_purchase,
    disarm_monthly_credit_reset_clock,
    is_credited_topup_transaction,
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
    ACCEPTED_CREDIT_TOPUP_PRICE_IDS,
    EXTRA_SEAT_PRICE_ID,
    PLAN_INTERVAL_TO_PRICE_ID,
    resolve_plan_for_price_id,
)
from app_server.paddle_webhook_verify import verify_paddle_signature

paddle_webhook_router = APIRouter()


def _line_items(data: dict) -> list[dict]:
    """`items` as a list of dicts, tolerating a malformed payload (non-list,
    non-dict entries) instead of crashing the handler into days of retries."""
    items = data.get("items")
    if not isinstance(items, list):
        return []
    return [item for item in items if isinstance(item, dict)]


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
# adjustment.created, adjustment.updated (the approval of a refund, which is
# when a top-up's credit is taken back), transaction.updated, and every name in
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


class PaddleWebhookAmountError(RuntimeError):
    """A real, signature-verified Paddle transaction whose paid amount
    can't be turned into a credit or commission - the customer paid but
    nothing was applied, so a human has to follow up."""


class PaddleTopupRefundedError(RuntimeError):
    """Paddle raised a refund or chargeback on a credit top-up. The credit
    is taken back only once the adjustment is approved (see
    _claw_back_refunded_topup); this alert is the early heads-up."""


class PaddleTopupClawbackError(RuntimeError):
    """An approved refund or chargeback of a credit top-up could not be fully
    taken back from the balance: either the buyer had already spent part of
    it, or the top-up predates the ledger recording amounts. A human has to
    decide what to do about the difference."""


class PaddleWebhookPaidSetupReleaseError(RuntimeError):
    """release_paid_setup itself failed while compensating for an earlier
    failure in the gated block (affiliate attribution / Live Wiki+Docs
    enqueue). The original failure still propagates either way - this is
    the heads-up that the compensating release may not have landed, so the
    claim could be stuck non-NULL, silently skipping the one-time build/
    attribution on every future retry with nothing else observing it."""


# Paddle's documented webhook delivery retry window is "up to three days"
# (see the delivery-retry comment elsewhere in this file). rq's own
# DEFAULT_RESULT_TTL is 500 seconds: that's how long a finished job's
# Redis hash key survives, and unique=True's dedup (save_unique_job) is a
# plain EXISTS check against that same key - once it expires, a retry's
# re-enqueue under the identical job_id is no longer deduped at all and
# silently creates a second real job. Real bug this closes: without an
# explicit, longer result_ttl, the dedup below only actually holds for
# ~8 minutes, not the multi-day window a Paddle retry can legitimately
# land within - a retry that lands later than that reproduces the exact
# duplicate-LLM-spend failure the dedup exists to prevent. 4 days gives
# margin over the documented 3-day window.
_PAID_SETUP_JOB_RESULT_TTL_SECONDS = 4 * 24 * 60 * 60


def _enqueue_paid_setup_job(queue, job_name: str, job_id: str, *, installation_id: int) -> None:
    """Enqueues one of the two one-time paid-setup jobs (Live Wiki or Docs
    full build), tolerating a retry that re-enqueues the same,
    already-succeeded job under the same job_id - see
    _PAID_SETUP_JOB_RESULT_TTL_SECONDS for why result_ttl is set
    explicitly. Shared by both call sites so the dedup settings can't
    drift between them the way the plain fixed/count batch size once did
    elsewhere in this codebase."""
    from rq.exceptions import DuplicateJobError

    try:
        queue.enqueue(
            job_name,
            job_timeout=60,
            result_ttl=_PAID_SETUP_JOB_RESULT_TTL_SECONDS,
            installation_id=installation_id,
            job_id=job_id,
            unique=True,
        )
    except DuplicateJobError:
        pass


# Adjustment actions that return a customer's money. Others ("credit",
# "chargeback_reverse", "chargeback_warning", ...) do not.
_MONEY_RETURNING_ADJUSTMENT_ACTIONS = {"refund", "chargeback"}


def _finite_decimal(value) -> Decimal | None:
    try:
        parsed = Decimal(str(value))
    except InvalidOperation:
        return None
    return parsed if parsed.is_finite() else None


def _topup_credit_usd(topup_item: dict, totals: dict) -> Decimal | None:
    """USD of top-up credit one transaction bought, or None if the payload
    can't be trusted to price it.

    Never derived from totals.total: that is in the customer's checkout
    currency (a $5 top-up checked out in INR arrives as 47785, i.e. Rs
    477.85, and would have been credited as $477.85) and includes any tax
    added on top. The price is $1.00 USD per unit, so the credit is the
    quantity scaled by the share of the pre-tax subtotal that was not
    discounted - a ratio, so it holds in any currency and with any tax mode.
    """
    quantity = _seat_item_quantity(topup_item)
    subtotal = _finite_decimal(totals.get("subtotal"))
    raw_discount = totals.get("discount")
    discount = Decimal(0) if raw_discount is None else _finite_decimal(raw_discount)
    if quantity <= 0 or subtotal is None or subtotal <= 0 or discount is None:
        return None
    if discount < 0 or discount > subtotal:
        return None
    return (Decimal(quantity) * (subtotal - discount) / subtotal).quantize(
        Decimal("0.01"), rounding=ROUND_HALF_UP
    )


def _usd_earnings_minor_units(data: dict) -> tuple[Decimal | None, bool]:
    """(Paddle earnings in USD cents, whether the amount was unavailable
    only because the transaction is not in USD). Earnings are what Paddle
    pays out after tax and its own fee - the base for affiliate commission,
    so an affiliate is never paid on money that goes to the tax authority or
    to Paddle. Prefers Paddle's own conversion into the USD payout currency
    (details.payout_totals) so a non-USD checkout is commissioned on its USD
    value, never on its local-currency number."""
    details = data.get("details") or {}
    payout = details.get("payout_totals") or {}
    totals = details.get("totals") or {}
    for source in (payout, totals):
        if source.get("currency_code") == "USD":
            return _finite_decimal(source.get("earnings")), False
    return None, True


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
    if event_type in ("adjustment.created", "adjustment.updated") or (
        event_type == "transaction.updated"
        and (payload.get("data") or {}).get("status")
        in {"refunded", "partially_refunded", "charged_back"}
    ):
        await _handle_adjustment_created(payload.get("data") or {}, pool, event_type)
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
        # Same CHECKOUT_TOKEN_WEBHOOK_MAX_AGE as _handle_transaction_completed's
        # top-up path below, and for the identical reason: this token is minted
        # at the same checkout-click moment, and Paddle retries a failed
        # delivery for up to three days - the default 30-minute
        # CHECKOUT_TOKEN_TTL would make a delayed subscription.created/updated
        # webhook silently fail to flip the installation's plan.
        unsign_checkout_installation_id(
            installation_token, get_settings().session_secret, max_age=CHECKOUT_TOKEN_WEBHOOK_MAX_AGE
        )
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

    items = _line_items(data)
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
                    conn, installation_id, extra_seats - previous_extra_seats, plan, extra_seats, is_annual
                )

            if plan != "free":
                transitioned_to_paid = await claim_free_to_paid_plan(conn, installation_id, plan)
                if not transitioned_to_paid:
                    await set_paid_installation_plan(conn, installation_id, plan)
            else:
                await set_installation_plan(conn, installation_id, plan)
                # A cancel/pause/past-due transition is the one place a
                # previously-armed annual-AIR monthly clock is never
                # otherwise disarmed - see disarm_monthly_credit_reset_
                # clock's own docstring for what leaving it armed causes.
                await disarm_monthly_credit_reset_clock(conn, installation_id)

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
    paid_setup_claimed_at = (
        await claim_paid_setup(pool, installation_id) if plan != "free" else None
    )

    if paid_setup_claimed_at is not None:
        try:
            # Attribution: first time this installation goes free -> paid, on
            # the AIR plan only (affiliates are AIR-only: Flash's margin cannot
            # carry a recurring 15% commission) - if it was checked out with a
            # known affiliate's discount code, credit that affiliate. Gated on
            # the same paid-setup claim as the AIRview/Docs build below, so a
            # later subscription.updated for the same installation (e.g.
            # switching monthly <-> annual) can't re-attribute or steal credit
            # - record_referral is also itself a database-enforced no-op past
            # the first row (installation_id is that table's primary key).
            discount_id = (data.get("discount") or {}).get("id")
            if discount_id and plan == "air":
                affiliate = await get_affiliate_by_discount_id(pool, discount_id)
                if affiliate is not None:
                    await record_referral(pool, installation_id, affiliate["id"])

            # One-time Live Wiki + Docs build - fires exactly once, on the
            # free -> paid transition. Without this, installations upgraded
            # through Paddle (the only real payment path this app has - see
            # claim_paid_setup) never get an initial AIRview build at all, and
            # the wiki would stay limited to whatever clusters an incremental
            # push happened to touch after the fact.
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

                # Deterministic, installation-scoped job_id + unique=True +
                # an explicit result_ttl (see _PAID_SETUP_JOB_RESULT_TTL_
                # SECONDS): real bug this closes - the two enqueues below
                # aren't atomic, so if the wiki enqueue succeeds and the
                # docs enqueue then fails (a Redis blip), the except block
                # below releases the claim and re-raises so Paddle retries.
                # That retry used to call BOTH enqueues again from scratch,
                # including the wiki build that had already succeeded - a
                # duplicate full AIRview build with real LLM spend. With a
                # stable job_id and unique=True, rq's atomic check-and-push
                # (save_unique_job) raises DuplicateJobError instead of
                # re-queueing a second job under the same id, so the
                # retry's re-enqueue of the already-succeeded wiki build is
                # a safe no-op and only the docs build (which never
                # actually queued) runs for real - as long as the retry
                # lands before result_ttl expires, which is why that's set
                # explicitly here rather than left at rq's 500-second
                # default.
                _enqueue_paid_setup_job(
                    queue,
                    "scan_worker.jobs.run_live_wiki_full_build_for_installation_job",
                    f"paid-setup-wiki-{installation_id}",
                    installation_id=installation_id,
                )
                _enqueue_paid_setup_job(
                    queue,
                    "scan_worker.jobs.run_live_docs_full_build_for_installation_job",
                    f"paid-setup-docs-{installation_id}",
                    installation_id=installation_id,
                )
        except BaseException:
            # BaseException, not Exception: a request cancellation
            # (asyncio.CancelledError, a BaseException subclass since
            # Python 3.8 - e.g. a worker restart or client disconnect
            # mid-call) must still release the claim below, or it never
            # does and paid_setup_claimed_at stays stuck non-NULL forever -
            # every later retry's claim_paid_setup then returns None and
            # the one-time build/attribution is silently skipped for good,
            # with nothing observing it. Always re-raised at the end
            # regardless (including CancelledError), so cancellation still
            # propagates normally.
            #
            # The claim above is already committed. If the work it gates
            # fails (Redis/DB blip), a Paddle retry would find the claim
            # consumed and "succeed" without ever running the one-time
            # build/attribution. Hand the claim back so the retry reruns
            # it - compare-and-set on the exact timestamp this call's own
            # claim set, so a newer claim that raced past this failure
            # isn't the one that gets released (see release_paid_setup's
            # docstring).
            try:
                # Shielded: this is itself an await inside an except block, so a second cancellation
                # (the same task's cancellation re-delivered, or a shutdown after a first one) would
                # raise CancelledError right here and abandon the release half-done - the claim
                # stuck non-NULL, the exact failure this handler exists to prevent. Shielded, the
                # release runs to completion even if this task is cancelled again.
                await asyncio.shield(release_paid_setup(pool, installation_id, paid_setup_claimed_at))
            except Exception as release_exc:
                # Most plausible exactly when the original failure above
                # was itself a DB outage - the same outage that broke the
                # gated work can break this compensating release too. Don't
                # let that mask the original failure (still re-raised
                # below) or fail silently; this is the one signal ops gets
                # that the claim might be stuck.
                send_error_alert(
                    "paddle_webhook",
                    PaddleWebhookPaidSetupReleaseError(
                        f"installation {installation_id}: failed to release paid-setup claim "
                        f"after an earlier failure ({type(release_exc).__name__}: {release_exc})"
                    ),
                    f"installation_id={installation_id}",
                )
            raise

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
    and only if the paying installation has a referral on file, the
    transaction pays for the AIR plan (or its extra seats), AND the
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
    items = _line_items(data)
    # Raw (pre-filter) count of whatever `items` the payload actually carried,
    # malformed entries included. The "exactly one item" bundling guard below
    # must be judged against this, not against `items` (which _line_items has
    # already dropped malformed entries from) - otherwise a payload shaped
    # like [{a real topup item}, None] (2 raw items, 1 malformed) collapses to
    # len(items) == 1, passes the guard, and gets auto-credited for the full
    # transaction total even though a second, unparseable line item could have
    # carried real cost. Falls back to 0 when `items` itself isn't a list -
    # not len(items), which would always be 0 anyway here: items comes from
    # _line_items(data) reading this same data.get("items"), and returns []
    # on exactly the same non-list condition this falls back on.
    raw_items = data.get("items")
    raw_item_count = len(raw_items) if isinstance(raw_items, list) else 0
    topup_item = next(
        (item for item in items if (item.get("price") or {}).get("id") in ACCEPTED_CREDIT_TOPUP_PRICE_IDS),
        None,
    )
    installation_token = (data.get("custom_data") or {}).get("installation_token")
    installation_id = (
        unsign_checkout_installation_id(
            installation_token, get_settings().session_secret, max_age=CHECKOUT_TOKEN_WEBHOOK_MAX_AGE
        )
        if installation_token
        else None
    )
    if installation_id is None:
        # Most transactions legitimately carry no usable token and are a
        # silent no-op here. A paid credit top-up that can't be attributed is
        # different: money was collected and nothing was credited, so say so
        # rather than letting it look like a success.
        if topup_item is not None:
            logger.warning(
                "credit top-up transaction.completed has a missing or invalid installation_token: %s",
                data.get("id"),
            )
            send_error_alert(
                "paddle_webhook",
                PaddleWebhookAttributionError("paid credit top-up has a missing or invalid installation_token"),
                f"transaction_id={data.get('id')}",
            )
        return

    # A customer-purchased credit top-up. This still needs to run before the
    # referral lookup below (rather than after an early return on "no
    # referral"), because a referred installation's top-up must still be
    # credited even though - see the early return at the end of this block -
    # it is deliberately excluded from earning its referrer any commission.
    if topup_item is not None:
        # Credit the amount Paddle actually COLLECTED for this transaction,
        # not the line item's quantity - quantity assumes exactly $1 of
        # credit per unit and silently ignores any discount. A top-up
        # transaction never bundles a top-up with any other line item (see
        # the buyCredit() comment below), so details.totals.total - Paddle's
        # collected amount net of discount, in the currency's minor unit, as
        # a string - IS the real dollar amount collected for this top-up,
        # PROVIDED the top-up is the only item. This codebase has never
        # parsed Paddle's per-line-item totals, only the transaction-level
        # one, so a transaction with the top-up item plus anything else has
        # no way to isolate just the top-up's share here - crediting the
        # whole total in that case would over-credit by whatever the other
        # item(s) cost. buyCredit() itself never sends more than one item,
        # but nothing server-side enforced that until this check: a
        # devtools-crafted Paddle.Checkout.open() call could otherwise bundle
        # a top-up with another price and get topped up for the combined
        # amount. Guarded rather than parsed because there has never been a
        # real transaction shaped this way to build correct per-item parsing
        # against - skip and log instead of guessing.
        transaction_id = data.get("id")
        totals = (data.get("details") or {}).get("totals") or {}
        if raw_item_count != 1:
            logger.warning(
                "credit topup transaction.completed bundled with other line items, "
                "skipping to avoid over-crediting: %s",
                data.get("id"),
            )
        elif transaction_id:
            amount_usd = _topup_credit_usd(topup_item, totals)
            if amount_usd is None:
                logger.warning(
                    "credit topup transaction.completed has a missing or inconsistent "
                    "quantity/subtotal/discount, not crediting: %s",
                    data.get("id"),
                )
                send_error_alert(
                    "paddle_webhook",
                    PaddleWebhookAmountError("credit top-up could not be priced from its payload"),
                    f"transaction_id={transaction_id} installation_id={installation_id}",
                )
            elif amount_usd > 0:
                await credit_topup_purchase(
                    pool, installation_id, float(amount_usd), transaction_id,
                    charged_total_minor=_finite_decimal(totals.get("total")),
                )
        else:
            logger.warning(
                "credit topup transaction.completed missing id: %s",
                data.get("id"),
            )
        return

    referral = await get_referral(pool, installation_id)
    if referral is None:
        return

    # Affiliates are AIR-only: a referred installation that later moves to
    # Flash stops earning commission. Decided from this transaction's own
    # line items (not the installation's current plan) so the first payment
    # is still commissioned even if this event arrives before the
    # subscription event that flips the plan. The extra-seat add-on only
    # exists on AIR subscriptions.
    if not any(
        (price_id := (item.get("price") or {}).get("id")) == EXTRA_SEAT_PRICE_ID
        or resolve_plan_for_price_id(price_id) == "air"
        for item in items
    ):
        return

    transaction_id = data.get("id")
    earnings_minor_units, non_usd = _usd_earnings_minor_units(data)
    billed_at_raw = data.get("billed_at") or data.get("created_at")
    if transaction_id and non_usd:
        logger.warning(
            "transaction.completed for a referred installation is in a non-USD currency "
            "with no USD payout total, skipping commission: %s",
            transaction_id,
        )
        send_error_alert(
            "paddle_webhook",
            PaddleWebhookAmountError("affiliate commission skipped: non-USD transaction without a USD payout total"),
            f"transaction_id={transaction_id} installation_id={installation_id}",
        )
        return
    if not transaction_id or earnings_minor_units is None or not billed_at_raw:
        logger.warning(
            "transaction.completed for a referred installation is missing fields "
            "needed for commission calculation"
        )
        return

    try:
        billed_at = datetime.fromisoformat(billed_at_raw)
    except ValueError:
        logger.warning("transaction.completed has an unparseable billed_at")
        return

    commission_usd = (earnings_minor_units / Decimal(100) * _AFFILIATE_COMMISSION_RATE).quantize(
        Decimal("0.01"), rounding=ROUND_HALF_UP
    )
    # Same details.totals.total the credit-topup branch above reads earlier
    # in this function - a refund/chargeback adjustment's own totals.total
    # arrives in this same original currency, so reverse_commission_partial
    # can prorate against it later (see its docstring).
    charged_total_minor = _finite_decimal(((data.get("details") or {}).get("totals") or {}).get("total"))

    await record_commission(
        pool,
        referral["affiliate_id"],
        installation_id,
        transaction_id,
        commission_usd,
        billed_at,
        charged_total_minor=charged_total_minor,
    )


async def _handle_adjustment_created(data: dict, pool, event_type: str = "adjustment.created") -> None:
    """Reverse a commission when Paddle refunds or charges back a transaction,
    and take a credit top-up's credit back once that is approved."""
    transaction_id = (
        data.get("transaction_id")
        or data.get("id")
        or (data.get("transaction") or {}).get("id")
    )
    if not transaction_id:
        return

    action = data.get("action")
    money_returning = action is None or action in _MONEY_RETURNING_ADJUSTMENT_ACTIONS
    is_topup = money_returning and await is_credited_topup_transaction(pool, transaction_id)

    # The first sighting of an adjustment (and a transaction.updated status
    # change) does the commission reversal and the heads-up alert. A later
    # adjustment.updated for the same refund only carries the approval, so it
    # must not repeat either.
    if event_type != "adjustment.updated":
        from app_server.affiliates import reverse_commission_partial

        if action is not None:
            # A real adjustment.created event: data["id"] is the adjustment's
            # own id (same field _claw_back_refunded_topup keys on below) and
            # data.totals.total is the amount *this* adjustment returned, in
            # the transaction's original currency - enough to prorate. Either
            # can be missing on an unexpectedly-shaped payload; left as None
            # rather than guessed at here, so reverse_commission_partial can
            # tell "no commission on this transaction at all" (the common
            # case, not worth alerting on) apart from "a commission exists
            # but this payload can't apply to it" (below).
            commission_adjustment_id = data.get("id")
            refunded_total_minor = _finite_decimal((data.get("totals") or {}).get("total"))
        else:
            # transaction.updated: carries only the transaction's own
            # id/current status, no adjustment id and no incremental
            # refunded amount to prorate against - expected for this path,
            # not an anomaly. Reverses the whole remaining commission in one
            # shot, same as the old all-or-nothing behavior - keyed on the
            # transaction id itself so a repeated status-change delivery
            # doesn't reverse it twice.
            commission_adjustment_id = f"txn-status:{transaction_id}"
            refunded_total_minor = Decimal("Infinity")

        commission_result = await reverse_commission_partial(
            pool, commission_adjustment_id, transaction_id, refunded_total_minor
        )
        if commission_result["status"] in ("missing_adjustment_id", "unknown_amount"):
            # A real commission exists for this transaction (reverse_commission_partial
            # already checked - "not_found" never reaches here) but this
            # payload didn't carry what's needed to apply or dedupe the
            # reversal. Paddle only retries on a non-2xx response, this event
            # already got one, and guessing (full reversal on an unknown
            # amount, or skipping silently) could either unfairly cost the
            # affiliate or permanently drop a real claw-back - alert for
            # manual follow-up instead, same conservative stance
            # _claw_back_refunded_topup takes for the identical gap on the
            # credit side.
            logger.warning(
                "commission reversal skipped for transaction %s (%s)",
                transaction_id, commission_result["status"],
            )
            send_error_alert(
                "paddle_webhook",
                PaddleWebhookAmountError(f"commission reversal skipped: {commission_result['status']}"),
                f"transaction_id={transaction_id} action={action}",
            )
        # transaction.updated payloads carry no adjustment action; they only
        # reach this handler for a refunded/charged-back status, so a missing
        # action means money was returned.
        if is_topup:
            logger.warning(
                "credit top-up %s has a refund or chargeback (status=%s); credit is taken back once it is approved",
                transaction_id, data.get("status"),
            )
            send_error_alert(
                "paddle_webhook",
                PaddleTopupRefundedError("credit top-up refund or chargeback raised"),
                f"transaction_id={transaction_id} action={action or 'transaction.updated'} "
                f"status={data.get('status')}",
            )

    if is_topup and data.get("id") and data.get("status") == "approved" and action is not None:
        await _claw_back_refunded_topup(data, transaction_id, pool)


async def _claw_back_refunded_topup(data: dict, transaction_id: str, pool) -> None:
    """Takes back the credit for an APPROVED refund or chargeback of a top-up.
    A pending refund can still be rejected, and a rejected one must leave the
    buyer's credit alone, so nothing here runs before approval."""
    adjustment_id = data["id"]
    refunded = _finite_decimal((data.get("totals") or {}).get("total"))
    if refunded is None or refunded <= 0:
        logger.warning(
            "approved adjustment %s for top-up %s has no usable refunded total, not taking credit back",
            adjustment_id, transaction_id,
        )
        send_error_alert(
            "paddle_webhook",
            PaddleTopupClawbackError("approved top-up refund has no usable refunded total"),
            f"transaction_id={transaction_id} adjustment_id={adjustment_id}",
        )
        return

    result = await claw_back_topup_credit(pool, adjustment_id, transaction_id, refunded)
    if result["status"] == "duplicate":
        return
    if result["status"] == "no_record":
        logger.warning("approved refund of top-up %s has no recorded grant to reverse", transaction_id)
        send_error_alert(
            "paddle_webhook",
            PaddleTopupClawbackError("approved top-up refund could not be reversed: no recorded grant"),
            f"transaction_id={transaction_id} adjustment_id={adjustment_id}",
        )
        return

    logger.info(
        "took back $%s of top-up credit for installation %s (refund %s of %s)",
        result["clawed_usd"], result["installation_id"], adjustment_id, transaction_id,
    )
    if result["shortfall_usd"] > 0:
        send_error_alert(
            "paddle_webhook",
            PaddleTopupClawbackError("refunded top-up credit was already spent"),
            f"transaction_id={transaction_id} adjustment_id={adjustment_id} "
            f"installation_id={result['installation_id']} shortfall_usd={result['shortfall_usd']:.2f}",
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
