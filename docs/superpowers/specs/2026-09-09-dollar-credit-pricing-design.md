# Dollar-Credit Pricing Design

## Problem

Aletheore's marketing currently promises fixed review counts - "up to 800/month"
for Flash Review on the Flash tier ($8/mo), with no explicit AIRview/Docs number
for AIR ($29.99/mo). These counts are *derived estimates* on top of a real,
already-enforced dollar cap (`PLAN_CAP_OVERRIDE_USD = {"flash": 6.00, "air":
20.00}` in `github-app/app_server/llm_cost.py`), using assumptions about average
tokens-per-review that don't hold for every repo. A customer with an unusually
large or complex repo could hit the real $ cap well before reaching their
"promised" review count - the promise and the enforcement mechanism are two
different things that can silently disagree.

Real measured costs (from a same-night benchmark comparing gpt-5.6-luna against
a candidate cheaper model on the same 24-case corpus) confirm the $ cap is the
right unit to promise: Luna costs ~$0.0049/review, so $5 of credit buys
~1,000 reviews - *more* generous than the current "800/month" claim, not less.

## Goal

Replace the review-count promise with a real, transparent dollar-credit balance
the customer can see, spend down, top up, and get notified about - matching the
mechanism the system already enforces instead of a derived estimate layered on
top of it.

- Flash: **$5/mo** included credit (plan price unchanged, $8/mo)
- AIR: **$18/mo** included credit (plan price unchanged, $29.99/mo)
- Both numbers leave real margin under Paddle's cut and the real enforced
  ceiling, and were checked against real per-review/per-build costs, not
  picked arbitrarily.

## Non-goals

- Migrating existing subscribers to a new balance mid-cycle - not needed.
  Confirmed via direct production DB query (2026-09-09): zero Flash-tier
  installations exist, and the only two AIR installations are the project's
  own dogfooding accounts. There is no real subscriber whose expectations
  this change could violate.
- Refunds or prorating a cancelled subscription's remaining balance.
- Changing what AIRview/Docs/Flash Review/managed audits actually cost to run.
- Annual billing for top-up credit (it's a one-time purchase regardless of
  the underlying subscription's billing interval).

## Current State (what this replaces)

- `PLAN_CAP_OVERRIDE_USD` (`github-app/app_server/llm_cost.py`): a flat,
  static $ ceiling per *plan*, identical for every installation on that plan.
  Not per-customer, no concept of a balance, no concept of purchasing more.
- `reserve_llm_spend(dsn, installation_id, reserve_usd, monthly_cap) -> bool`
  and `release_llm_spend_reservation` (`github-app/scan_worker/db.py`): the
  real, atomically-safe enforcement path. Every real LLM call site (Flash
  Review, AIRview incremental/full build, Docs incremental/full build,
  managed audits) reserves budget here *before* calling the model, and
  releases it if the call ultimately isn't made. Already has a real
  concurrency test (`test_reserve_llm_spend_is_atomic_under_real_concurrency`)
  proving two simultaneous reservations against the same installation can
  never together exceed the cap.
- `llm_spend` / `llm_spend_events` (`github-app/scan_worker/db.py`): pure
  *accounting* - what was actually spent, this month, and (as of the
  just-added `llm_spend_events` ledger) broken down by feature. This is a
  record of the past. It is not touched by this design except to keep
  writing to it exactly as it does today - accounting and balance-tracking
  are separate concerns.
- `installations` table already carries billing-derived numeric state
  directly as columns (`extra_seats`, `max_api_tokens`), and Paddle identity
  (`paddle_subscription_id`, `paddle_customer_id`). This design follows that
  existing pattern rather than introducing a new table for a 1:1 relationship.
- `github-app/app_server/webhooks/paddle.py` currently handles
  `subscription.created` and `subscription.updated` only. There is no
  handling today for a one-time (non-subscription) Paddle purchase.

## Decisions (from brainstorming)

1. **Top-up credit is a one-time purchase, never expires.** Not a recurring
   line item (unlike the existing extra-seat pattern). Matches RepoWise's own
   "packs, never expire" framing, confirmed with the user.
1a. **The amount is customer-chosen, not a fixed menu of packs.** One credit
   price at $1/unit, billed by quantity - the customer picks the quantity at
   checkout (quantity 8 = $8 of credit). This reuses the exact "billed by
   quantity against one price" mechanism `EXTRA_SEAT_PRICE_ID` already uses
   in this codebase, just as a one-time transaction instead of a recurring
   subscription line item, rather than requiring a customer to pick from a
   predefined set of pack sizes. A $5 minimum purchase applies so Paddle's
   fixed per-transaction processing fee doesn't eat a disproportionate share
   of a very small purchase; no maximum.
2. **The plan's base included credit resets every renewal**, independent of
   any top-up balance. It does not accumulate across cycles - unused base
   credit at the end of a cycle is gone, same as the current review-count
   promise implicitly worked (a customer never carried over unused reviews).
3. **Draw-down order: base credit first, then top-up balance.** A customer
   only spends from their purchased top-up once the month's included credit
   is exhausted.
4. **The old flat plan cap is retired as an enforcement mechanism, not kept
   as a ceiling on top of the new balance.** Once a customer buys top-up
   credit, their real spendable ceiling for that cycle is
   `base_credit_remaining + topup_credit_balance`, which can exceed the old
   $6/$20 figures by design - that's the entire point of selling top-ups.
   There is no separate "still capped at the old number even after buying
   more" behavior.
5. **Exhaustion still fails open, never overspends** - same philosophy as
   today (a job degrades gracefully, e.g. `set_wiki_build_status(...,
   "failed", ...)`, rather than blocking or erroring loudly). What changes is
   *visibility*: real customer-facing signals now exist where before there
   was only an internal log line.
6. **Two customer-facing emails**, both new:
   - **Low-balance warning**, fired once per cycle when remaining balance
     (base + top-up combined) crosses a threshold (proposed: 15% of that
     cycle's starting total remaining - see Open Question 1).
   - **Exhausted**, fired when a real reservation attempt is *rejected* for
     insufficient balance - not a raw `== 0` check. "Exhausted" means the
     next real call's expected cost no longer fits in what's left, which is
     exactly what a rejected `reserve_llm_spend` call already detects.
7. **Dashboard balance display + "buy more" flow ship in the same pass**,
   not deferred - the customer needs to be able to see the number being
   promised, or the whole redesign is less honest than the thing it replaces.

## Architecture

Two new columns on `installations`, atomically updated by the same reservation
path that already exists, plus one new billing-period marker column so the
renewal reset can be applied idempotently.

```
installations
  ...
  base_credit_remaining_usd     NUMERIC NOT NULL DEFAULT 0
  topup_credit_balance_usd      NUMERIC NOT NULL DEFAULT 0
  current_billing_period_start  TIMESTAMPTZ
  low_balance_email_sent_at     TIMESTAMPTZ   -- dedup, cleared on renewal
  exhausted_email_sent_at       TIMESTAMPTZ   -- dedup, cleared on renewal
```

Two dedup timestamp columns (rather than a single "which emails have I sent"
flag) so a customer who tops up mid-cycle after going low can still get a
fresh exhausted email later in the same cycle if they run out again - each
email type dedupes independently, both reset together on the next renewal.

### Why two stored columns instead of one, or instead of a live ledger sum

Covered in the brainstorming approaches comparison; recorded here for anyone
picking this spec up cold:

- **One combined column** can't distinguish "this leftover $2 came from base
  credit (must reset)" from "this leftover $2 came from a top-up (must
  persist)" at renewal time without tracking the split anyway - it either
  silently breaks the reset rule or collapses back into two numbers.
- **A live-summed ledger balance** (summing `llm_spend_events` and a new
  purchases table on every check) loses the atomic-under-concurrency
  guarantee the current system already has and is already tested for. Two
  simultaneous reservations could both read the same sum before either
  commits. Keep the ledger for audit/history (it already exists via PR #635's
  `llm_spend_events`); don't make it the hot enforcement path.

### Data flow: a real LLM call (unchanged shape, changed cap source)

1. A job (Flash Review, AIRview incremental update, etc.) is about to make a
   real LLM call.
2. It calls the reservation function with the installation's real remaining
   balance as the ceiling, instead of `base_cap_for_plan(plan)`'s flat
   constant.
3. On success: the reservation amount is deducted from
   `base_credit_remaining_usd` first; if that's insufficient, the remainder
   is deducted from `topup_credit_balance_usd`. Both updates happen inside
   the same atomic `UPDATE ... WHERE` the current `reserve_llm_spend` already
   uses, extended to two columns instead of one - single query, single
   transaction, same concurrency guarantee.
4. On success, check whether this reservation crossed the low-balance
   threshold (compare pre/post remaining total against the threshold) and
   whether `low_balance_email_sent_at` is unset for this cycle; if both,
   enqueue the low-balance email and stamp the timestamp.
5. On failure (would exceed remaining balance): same degrade-gracefully path
   every caller already has today (skip the job, log/set a failed status).
   Additionally, if `exhausted_email_sent_at` is unset for this cycle,
   enqueue the exhausted email and stamp the timestamp.
6. `record_llm_spend`/`llm_spend_events` continue to run exactly as today,
   unchanged - this design only changes what the *ceiling* is, not what gets
   recorded about what actually happened.

### Data flow: renewal (base credit reset)

Paddle's `subscription.updated` fires for many things unrelated to renewal
(seat changes, payment method updates) and webhooks are not ordered or
guaranteed-exactly-once - both real, documented Paddle behaviors. The reset
must therefore be scoped to genuine cycle boundaries and safe to process more
than once.

1. On `subscription.updated`, read `current_billing_period.starts_at` from
   the payload.
2. If it differs from the installation's stored `current_billing_period_start`
   (a real new cycle, not some other kind of update):
   - Reset `base_credit_remaining_usd` to the plan's base amount
     (`PLAN_BASE_CREDIT_USD = {"flash": 5.00, "air": 18.00}`, a new mapping
     alongside the existing `PLAN_MONTHLY_PRICE_USD`).
   - Leave `topup_credit_balance_usd` untouched - it never resets.
   - Clear both `low_balance_email_sent_at` and `exhausted_email_sent_at`.
   - Update the stored `current_billing_period_start` to the new value.
3. If it matches what's already stored, this is some other kind of
   `subscription.updated` (a seat change, etc.) - do nothing to the balance.
   Idempotent by construction: replaying the same renewal event twice is a
   no-op the second time, since the stored period start already matches.

### Data flow: a top-up purchase

New Paddle plumbing - no equivalent exists today.

1. Customer picks a dollar amount ($5 minimum) in the dashboard's "buy more"
   flow, which sets the quantity on a single one-time Paddle price
   (`CREDIT_TOPUP_PRICE_ID`, $1/unit) at checkout - not a subscription line
   item, and not a predefined pack.
2. Paddle sends `transaction.completed` for that one-time purchase. The
   webhook handler (new branch in `webhooks/paddle.py`) verifies the
   signature (existing mechanism, unchanged), confirms the price ID matches
   `CREDIT_TOPUP_PRICE_ID`, reads the real purchased amount from the
   transaction's quantity/total (not a lookup table, since the amount is
   customer-chosen), and attributes the purchase to an installation via
   `customer_id` matching `installations.paddle_customer_id` - the same
   attribution pattern subscription webhooks already use
   (`PaddleWebhookAttributionError` on a miss).
3. Atomically increments `topup_credit_balance_usd` by the purchased
   amount.
4. Idempotency: Paddle retries a webhook it didn't get a 2xx for, with the
   *same* `transaction.id`. A `processed_paddle_transactions` table (id
   primary key) recording every transaction ID this handler has already
   applied prevents a retry from crediting the same purchase twice - the
   same shape as `paddle:webhooks`' documented idempotency-ledger pattern for
   side effects that aren't naturally idempotent via UPSERT.

## Components

**Backend (this session, primary implementer: this agent):**
- Migration: new `installations` columns + `processed_paddle_transactions`
  table.
- `PLAN_BASE_CREDIT_USD` mapping (`llm_cost.py`).
- `CREDIT_TOPUP_PRICE_ID` (`paddle_pricing.py`): one real Paddle price,
  $1/unit, billed by customer-chosen quantity - the real price ID gets
  created live via the Paddle MCP, same as `EXTRA_SEAT_PRICE_ID`'s history.
- Rewrite `reserve_llm_spend`/`release_llm_spend_reservation` (`db.py`) to
  read/write the two balance columns on `installations` instead of the
  single `llm_spend` monthly total, base-first draw-down.
- New `webhooks/paddle.py` branch for `transaction.completed` on
  `CREDIT_TOPUP_PRICE_ID`, with the idempotency ledger.
- New `webhooks/paddle.py` renewal-reset branch on `subscription.updated`,
  scoped by `current_billing_period_start` comparison.
- Email-trigger hooks at the two points identified in the data flow above,
  reusing the existing job-side email/alert dispatch pattern this codebase
  already has for Slack/Teams alerts.
- Every existing call site of the old flat-cap check
  (`_llm_spend_cap_reached`, the various `reserve_llm_spend` calls in
  `jobs.py` for Flash Review, AIRview, Docs, managed audits) updated to the
  new per-installation signature.

**Dashboard + emails (this session, other agent):**
- Balance display component (`app_server/frontend.py`): current
  `base_credit_remaining_usd` + `topup_credit_balance_usd`, clearly
  distinguishing "included this month" from "purchased, never expires."
- "Buy more credit" flow: an amount input ($5 minimum) wired to Paddle
  checkout against `CREDIT_TOPUP_PRICE_ID` with quantity set from that
  amount.
- Email templates/copy for the two new notifications (low-balance,
  exhausted), triggered by the backend hooks above - this agent owns the
  content and rendering, not the trigger logic.

## Error handling

- **Unattributed top-up purchase** (a `transaction.completed` whose
  `customer_id` matches no installation): same `PaddleWebhookAttributionError`
  path the subscription webhooks already use - logged, non-2xx response so
  Paddle retries (the real fix is usually a timing issue - `pending_subscription_claims`
  hasn't resolved yet - and retrying recovers automatically).
- **Duplicate transaction delivery**: caught by the idempotency ledger before
  any balance mutation happens - a replay is a no-op, not a double-credit.
- **Renewal event processed twice**: idempotent by construction via the
  billing-period-start comparison - no separate ledger needed for this one.
- **A job's reservation is released** (call ultimately wasn't made): credits
  the exact amount back to whichever column(s) it was drawn from, same
  base-first/top-up-second bookkeeping in reverse.

## Testing

- Renewal reset only fires on a genuine new billing period, not on an
  unrelated `subscription.updated` (e.g. a seat-count change) - and is a
  no-op if the same renewal event is delivered twice.
- Draw-down order: a reservation that only partially fits in remaining base
  credit correctly spills the remainder into top-up balance, in one atomic
  update.
- Concurrency: two simultaneous reservations against the same installation's
  combined balance can never together exceed what's actually available -
  same style of test as the existing `test_reserve_llm_spend_is_atomic_under_real_concurrency`.
- Top-up webhook: a replayed `transaction.completed` (same transaction ID)
  credits the balance exactly once.
- Email dedup: the low-balance email fires once per cycle, not on every
  reservation after the threshold is crossed; same for the exhausted email;
  both re-arm after the next renewal.
- Release-on-failure correctly reverses a reservation's base/top-up split.

## Resolved Questions

1. **Low-balance threshold: 15%, confirmed.** Defined as 15% of a rolling
   high-water mark - the combined balance (base + top-up) immediately after
   the most recent event that raised it (a renewal reset or a top-up
   purchase, whichever happened most recently). `low_balance_email_sent_at`
   dedupes within that window; a later top-up purchase establishes a new,
   higher high-water mark and re-arms the check against the new 15%.
2. **Top-up amount: resolved, not open** - see Decision 1a. Customer-chosen
   quantity against one $1/unit price, $5 minimum, not a fixed pack menu.
