# Aletheore Paddle Subscription Claim Flow — Design Spec

## Overview

The marketing pricing page (`website/pricing.html`) already has a working Paddle Checkout integration (PR pending) — a visitor can pay for Indie/Team/Enterprise, monthly or yearly, with live regional pricing. What's missing is the other half: **nothing on Aletheore's side knows a payment happened, or which GitHub App installation it belongs to.** The marketing site has no authentication (sign-in lives on the separate `app.aletheore.com` dashboard), so a completed Paddle checkout carries no Aletheore identity at all today.

This spec covers the flow that closes that gap: a payment made on the unauthenticated marketing site gets carried, safely, to an authenticated claim step on the dashboard that actually applies the paid plan to the right installation — covering both a brand-new customer (no installation yet) and an existing free-plan customer upgrading.

## Goals

- A Paddle subscription purchased on the marketing site ends up applied to exactly one Aletheore installation, chosen by a signed-in human, not inferred or auto-matched.
- The webhook handler is the only thing that ever writes `installations.plan` — the plan value always comes from Paddle's own signed webhook payload, never from anything the browser asserts.
- Works for both a customer with zero existing installations (first-time buyer) and one with one or more (existing free user upgrading, or an org admin with multiple installs).
- No new self-service surface that could let one party redirect another party's paid subscription onto their own account.
- The webhook handler built here only needs to process `subscription.created` (the event that makes a claim exist in the first place). It's structured so `subscription.updated`/`subscription.canceled` can be added later using the same `paddle_subscription_id` lookup on `installations`, but this spec does not design the cancellation UI, the customer portal, or renewal-triggered downgrades — those are a separate follow-up.

## Non-Goals

- Detecting or canceling a duplicate/stale Paddle subscription if someone re-subscribes or switches tiers on an installation that already has a paid plan — this design just overwrites `plan` and the subscription ID (last write wins). A dedicated dedup/proration flow is out of scope here.
- Automated outreach (email reminders) for abandoned/unclaimed subscriptions. Unclaimed claims stay queryable for manual, human follow-up; no campaign infrastructure is built.
- A self-service "enter your payment email to reclaim" recovery form for the lost-cookie edge case. Deliberately not built — it's a realistic account-takeover-shaped risk (guess or learn someone else's payment email, redirect their paid subscription onto your own installation for free) for a low-frequency edge case that a manual support path handles fine at current scale.

## Architecture

**Claim token, generated client-side before checkout opens.** `website/paddle-checkout.js`'s `subscribe()` generates a random `claim_token` (crypto-secure), sets it as a cookie scoped to `.aletheore.com` (readable by `app.aletheore.com`, a different subdomain of the same parent domain), and passes it to Paddle as `custom_data: { claim_token }` on the checkout. Paddle persists `custom_data` on the resulting subscription and includes it in every webhook event for that subscription's lifetime, not just the first one.

**The webhook is authoritative.** A new handler, `github-app/app_server/webhooks/paddle.py`, receives `subscription.created` (and later `subscription.updated`/`subscription.canceled` for the ongoing relationship), verifies Paddle's webhook signature (mechanics pulled from the `paddle:webhooks` skill at implementation time — this is a hard requirement, not optional, since an unverified endpoint would let anyone POST a fake event and grant themselves a plan), resolves the Paddle `price_id` to an Aletheore plan name via a fixed mapping, and upserts a `pending_subscription_claims` row keyed by the `claim_token` from `custom_data`.

**`successUrl` redirects to `app.aletheore.com/subscribe/claim`.** That page reads the `claim_token` cookie (not a URL param — a cookie is only presented automatically by the browser that actually completed the purchase, so it can't leak the way a URL-embedded transaction ID could), requires GitHub sign-in, resolves the claim, and walks the signed-in user through applying it to the right installation.

## Data Model

New table:

```sql
CREATE TABLE pending_subscription_claims (
    id BIGSERIAL PRIMARY KEY,
    claim_token TEXT NOT NULL UNIQUE,
    paddle_subscription_id TEXT NOT NULL,
    paddle_customer_id TEXT NOT NULL,
    paddle_customer_email TEXT,
    plan TEXT NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    claimed_at TIMESTAMPTZ,
    claimed_by_installation_id BIGINT REFERENCES installations(installation_id)
);
CREATE INDEX pending_subscription_claims_token ON pending_subscription_claims (claim_token);
```

`installations` gains two columns: `paddle_subscription_id`, `paddle_customer_id`, both `NULL` until claimed. Once set, future webhook events for that subscription (renewals, cancellations) look the installation up directly by `paddle_subscription_id` — the claim token is only needed for the initial link-up, not the ongoing relationship.

The `price_id → plan` mapping is a small fixed dict in the webhook handler module (the six real price IDs from the already-created catalog, monthly+yearly × indie/team/enterprise), never inferred from client input.

## Claim Page UX (`app.aletheore.com/subscribe/claim`)

**State 1 — not signed in.** "Sign in with GitHub to activate your plan," reusing the dashboard's existing OAuth flow, returning to this same page afterward. The cookie survives the OAuth round-trip.

**State 2 — resolving the claim**, once signed in. Look up `pending_subscription_claims` by the cookie's token:
- **Not found yet** — a real race condition, since the redirect can beat webhook delivery. Poll every ~2s for up to ~20s with a "Confirming your payment…" state before showing a non-alarming "taking longer than expected, refresh shortly or contact support" message. The webhook will arrive in virtually all cases; this isn't treated as a failure.
- **Found, unclaimed** — proceed to State 3.
- **Already claimed** (revisit, double-click) — idempotent "already activated, go to dashboard."

**State 3 — installation selection:**
- **Zero installations**: prompt to install the GitHub App, then return to this same claim page (a new `installations` row now exists) to finish applying.
- **One installation**: explicit confirm required — "Apply Team to [org/repo]? [Confirm]." Not auto-applied silently, even in the single-installation case — mutating billing state without any user action is the wrong default regardless of how "obvious" the target looks.
- **Multiple installations**: pick from a list, then confirm.

**State 4 — confirmed**: updates `installations.plan` + the two Paddle ID columns, marks the claim row `claimed_at`/`claimed_by_installation_id`, clears the cookie, success screen linking to the dashboard.

## Abandoned Payment / Lost Cookie

A claim row never expires or gets deleted — the underlying subscription is real and actively billing, so there's no correctness reason to ever refuse a legitimate late claim. The only real risk is narrower than "they never came back": the cookie gets cleared before they claim (different browser, cleared storage). For that, no automated self-service recovery — a "payment didn't apply? contact support" path, backed by an admin-only lookup (query `pending_subscription_claims` by `paddle_customer_email`) that a human resolves manually. Automating this later is easy if it becomes a frequent support burden; it isn't a case worth building abuse-resistant self-service for yet.

## Security

- **Webhook signature verification is mandatory**, not optional — this is the sole gate preventing a forged webhook from granting a free plan. Implementation detail pulled from the `paddle:webhooks` skill.
- **Cookie over URL param** for carrying the claim token specifically because a cookie is scoped to the browser that completed the purchase and isn't exposed in browser history, referrer headers, or analytics the way a URL query parameter would be.
- **No client-asserted plan values** anywhere in this flow — the claim page only ever displays and lets a user *apply* a plan that the webhook already resolved and stored server-side from Paddle's own payload.

## Testing Strategy

- Webhook signature verification: valid signature accepted, invalid/missing signature rejected with no DB write.
- `price_id → plan` mapping: every real price ID from the catalog resolves to the correct plan; unknown price ID is rejected/logged, not silently defaulted.
- Claim resolution: not-found (polling state), found-unclaimed, already-claimed — all three paths.
- Installation selection: zero/one/many installations, each producing the right UI path and, on confirm, the right DB write.
- Race condition: claim page requests arriving before the webhook has landed, verified via the polling/retry behavior rather than a hard failure.
- End-to-end: real sandbox checkout → webhook → claim page → confirm → verify `installations.plan` updated, matching this session's established practice of real verification over trusting a self-reported "should work."
