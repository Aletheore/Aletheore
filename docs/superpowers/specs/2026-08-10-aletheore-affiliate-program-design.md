# Aletheore Affiliate Program Design

## Goal

Let a small, manually-onboarded group of content creators earn 15% recurring
commission on every customer they refer, for as long as that customer stays
subscribed to Aletheore AIR — using a real Paddle discount code as both the
customer-facing hook (10% off the first month) and the attribution mechanism,
with no new checkout UI, cookies, or query-param threading required.

## Background

Aletheore has one paid plan today (AIR, $29.99/mo + $4.99/mo per extra
seat), billed through Paddle. Checkout happens on `app.aletheore.com`
(`app_server/frontend.py`'s `/subscribe` route), which calls
`Paddle.Checkout.open()` with `customData: {installation_id}`.
`app_server/webhooks/paddle.py` already handles `subscription.created`,
`subscription.updated`, `subscription.canceled`, `subscription.paused`, and
`subscription.resumed` — reading `custom_data.installation_id` off each
event to keep `installations.plan` in sync. It does **not** currently
handle `transaction.completed`, so there is no stored record anywhere
today of the actual dollar amount Paddle collects per billing cycle.

Paddle itself has no built-in affiliate/referral program (it's billing
infrastructure, not a marketing platform), but its Discounts API supports
everything this needs natively: a merchant-defined code, a percentage
discount restricted to exactly one billing period (`recur: true`,
`maximum_recurring_intervals: 1`), and a `discount_id` that appears
directly on every subsequent transaction for that subscription. That
`discount_id` is the attribution key this design uses — no custom
tracking link, cookie, or session state needed anywhere.

## Scope decisions (from brainstorming)

- **Build custom, not a third-party affiliate platform.** A third-party
  platform (Rewardful/FirstPromoter/Tapfiliate) mostly sells self-serve
  signup, automated payouts, and affiliate-facing dashboards — none of
  which this program needs given the choices below. What's left to build
  is small and reuses the webhook infrastructure already in production.
- **Affiliates are manually onboarded**, a small handful of creators, not
  an open self-serve program. No public signup page, no affiliate login.
- **Payouts are manual and off-platform.** The system computes and
  displays what's owed; no money moves through the app. Building payout
  automation (Stripe Connect, PayPal Payouts, KYC) is a separate, much
  larger project not justified for a handful of partners.
- **Commission is 15% of the full transaction amount** Paddle actually
  collects (base plan + extra seats, net of that transaction's own
  discount), not just the base plan price.
- **Reporting is one simple admin-only page** — no self-serve affiliate
  dashboard.
- **Attribution is the Paddle discount code itself**, not a tracked link.
  A customer manually enters the creator's code at Paddle's own checkout.

## How it works

1. **Onboarding a creator.** An admin route calls Paddle's
   `discounts.create` API with a merchant-chosen code (e.g. `SARAH10`),
   `type: "percentage"`, `amount: "10"`, `recur: true`,
   `maximum_recurring_intervals: 1`, `enabled_for_checkout: true`. The
   returned discount ID (`dsc_...`) is stored locally against the new
   `affiliates` row. The creator is handed their code; nothing else is
   configured.

2. **A referred signup.** The customer types the code into Paddle's
   existing checkout UI (no product-side changes needed) and gets 10%
   off their first month.

3. **Attribution.** On the existing `subscription.created` webhook, the
   first time a given installation transitions free → paid, if
   `data.discount_id` matches a known affiliate's `paddle_discount_id`,
   a row is written linking that installation to that affiliate —
   first-touch, permanent, enforced by a unique constraint so it can
   never be silently overwritten by a later event.

4. **Commission.** `webhooks/paddle.py` gains a new branch handling
   `transaction.completed` (not handled today). If the transaction's
   installation has a referral row, 15% of `data.details.totals.total`
   is recorded as a commission, deduplicated on Paddle's transaction ID
   (Paddle retries webhook delivery on any non-2xx response, so this
   dedup is required, not optional).

5. **Getting paid.** A new admin report page lists each affiliate, how
   many installations they've referred, total commission accrued, and
   how much has been marked paid. The admin pays affiliates manually
   (bank transfer, PayPal, etc.) outside the app, then marks the
   corresponding commission rows as paid.

## Data model

```sql
CREATE TABLE affiliates (
    id                  BIGSERIAL PRIMARY KEY,
    code                TEXT NOT NULL UNIQUE,       -- mirrors the Paddle discount's own code, for display
    paddle_discount_id  TEXT NOT NULL UNIQUE,        -- dsc_... - the actual attribution key on webhooks
    name                TEXT NOT NULL,               -- admin's own reference for who this is
    created_at          TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE affiliate_referrals (
    installation_id  BIGINT PRIMARY KEY REFERENCES installations(installation_id) ON DELETE CASCADE,
    affiliate_id     BIGINT NOT NULL REFERENCES affiliates(id),
    referred_at      TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE affiliate_commissions (
    id                    BIGSERIAL PRIMARY KEY,
    affiliate_id          BIGINT NOT NULL REFERENCES affiliates(id),
    installation_id       BIGINT NOT NULL REFERENCES installations(installation_id) ON DELETE CASCADE,
    paddle_transaction_id TEXT NOT NULL UNIQUE,
    amount_usd            NUMERIC(10,2) NOT NULL,
    transaction_date      TIMESTAMPTZ NOT NULL,
    paid                  BOOLEAN NOT NULL DEFAULT false
);
```

`installation_id` as the primary key on `affiliate_referrals` is what
makes "first-touch, never overwritten" an `INSERT ... ON CONFLICT DO
NOTHING` rather than something application code has to remember to
check. `paddle_transaction_id UNIQUE` on commissions does the same job
for webhook retries.

## Components

- **Migration** — the three tables above.
- **`app_server/paddle_client.py`** — new `create_discount(api_key,
  code, description)` function, following the existing
  `get_subscription`/`create_portal_session`/`update_subscription_items`
  pattern (same base URL, same `_headers` helper, same
  `PaddleAPINotConfigured` error on a missing API key).
- **`app_server/affiliates.py`** (new module) — DB helpers:
  `create_affiliate`, `get_affiliate_by_discount_id`,
  `record_referral`, `record_commission`, `list_affiliates_with_totals`
  (for the report).
- **`app_server/webhooks/paddle.py`** — extended:
  - `subscription.created` branch (existing): on a free→paid
    transition, if `data.discount_id` resolves to a known affiliate,
    call `record_referral`.
  - New `transaction.completed` branch: if the installation has a
    referral row, call `record_commission` with 15% of the transaction
    total.
- **`app_server/admin.py`** — new routes:
  - `POST /admin/affiliates` — create a new affiliate (calls Paddle,
    then stores the result).
  - `GET /admin/affiliates` — the report page (affiliate, referral
    count, total owed, total paid).
  - `POST /admin/affiliates/{id}/mark-paid` — marks a set of commission
    rows as paid.

## Explicitly out of scope for this pass

- **Retroactive attribution.** A customer who already subscribed before
  this program existed can't be attributed after the fact — there's no
  `transaction.completed` history stored before this ships. Only new
  subscribers going forward can be attributed, which matches the
  manual-onboarding model (affiliates get credit for people they
  actually bring in via their code, not existing customers).
- **Refunds and chargebacks.** If Paddle refunds a transaction that
  already generated a commission row, this design does not automatically
  claw it back. Given manual payouts, the admin is expected to account
  for this by eye when actually paying someone. Automatic reconciliation
  can be added later if refund volume ever makes manual tracking
  unreliable.
- **Fraud/abuse detection** (self-referral, code sharing beyond the
  intended creator). Not a v1 concern given the small, manually-vetted
  group of affiliates.
- **Self-serve affiliate signup, login, or dashboard.** Affiliates never
  authenticate into Aletheore; they only ever see their code and
  whatever the admin tells them directly.
- **Automated payouts.** No payment-out integration of any kind.

## Testing

- `paddle_client.create_discount` — mocked Paddle API call, same style
  as existing `paddle_client` tests.
- `affiliates.py` DB helpers — real-Postgres tests via the existing
  `pool` fixture, covering: creating an affiliate, recording a referral
  (and that a second referral for the same installation is a no-op),
  recording a commission (and that a duplicate `paddle_transaction_id`
  is a no-op), and the totals query.
- `webhooks/paddle.py` — extend existing webhook tests: a
  `subscription.created` event with a `discount_id` matching a known
  affiliate creates a referral; one with an unrecognized `discount_id`
  does not error and does not create a referral; a `transaction.completed`
  event for a referred installation creates a commission at exactly 15%
  of the transaction total; a repeated `transaction.completed` (same
  `paddle_transaction_id`) does not double-count.
- Admin routes — standard route tests following the existing admin.py
  patterns (auth required, correct response shape).
