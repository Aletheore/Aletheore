# Manual Account Changes

**Purpose:** Define the safe procedure for changing an installation's plan or account state directly in the database, outside the normal Paddle-driven flow.
**Status:** Active baseline
**Owner:** Arihant Kaul
**Related Documents:** [README.md](README.md), [INCIDENT-RESPONSE.md](INCIDENT-RESPONSE.md), [DATA-HANDLING.md](DATA-HANDLING.md)
**Last Updated:** 2026-09-22

## Purpose

Every legitimate plan change in this codebase happens inside one transaction in `github-app/app_server/webhooks/paddle.py`, triggered by a real Paddle `subscription.updated` (or equivalent) webhook. That transaction does three things together: it updates `installations.plan`, and — whenever the event carries a genuine, newer `current_billing_period.starts_at` — it also calls `reset_billing_period_credit`, which resets `base_credit_remaining_usd` **and** `base_credit_allotment_usd` to the new plan's real included credit (see `PLAN_BASE_CREDIT_USD` in `github-app/app_server/llm_cost.py`).

A direct SQL `UPDATE installations SET plan = ...` — for an internal/dogfooding account, a support fix, or any other manual intervention — changes the plan but skips that entire transaction. Nothing else in the codebase resets credit on a plan change; `set_installation_plan`, `claim_free_to_paid_plan`, and `set_paid_installation_plan` (the only three functions that ever write `plan`) are called exclusively from that one webhook handler. The result: an installation whose plan was changed manually keeps its **old** plan's credit allotment/balance, frozen wherever it happened to be, with no automatic path that will ever refill it — `run_monthly_credit_reset_sweep_job` (the scheduled sweep) only touches rows where `next_monthly_credit_reset_at` is set, which itself is a byproduct of a real Paddle-driven reset.

## Real Incident (2026-09-22)

Aletheore's own dogfooding install (`Aletheore/Aletheore`, installation_id `147514632`) had its plan changed from `air` to `flash` via a direct database write, not a real Paddle downgrade. Confirmed live on production:

- `base_credit_allotment_usd` stayed at `18.00` (air's allotment) instead of resetting to flash's `5.00`.
- `base_credit_remaining_usd` and `topup_credit_balance_usd` were both `0` — fully drained from before the plan change, never refilled.
- `current_billing_period_start` was `NULL` for this row, so the monthly reset sweep had nothing to act on.

Effect: `run_flash_review_job` (`github-app/scan_worker/jobs.py`) calls `reserve_llm_spend_with_email_hooks` before running a real review, which correctly rejects the reservation whenever the combined balance is `$0` — so every Flash Review attempt on this repo silently no-opped (job completes in tens of milliseconds, no comment posted, no error logged) for as long as the balance stayed drained. This is the system working as designed for an exhausted balance; the balance was just never supposed to be at zero for this plan.

Separately, the same manual plan change made `health_check_targets`'s one row for this installation silently ineligible for the health-check sweep (`list_health_check_targets_all` is deliberately AIR-exclusive — a real, live JOIN, not a snapshotted value, so this side self-corrects automatically and needed no manual fix). Only the credit balance is a snapshotted value that requires the explicit reset call.

**Fixed by:** a one-time manual credit reset (`base_credit_allotment_usd = 5.00`, `base_credit_remaining_usd = 5.00`, matching flash's real `PLAN_BASE_CREDIT_USD`), not a code change — the Paddle-driven reset path itself was verified correct and untouched.

## Procedure

Whenever `installations.plan` is changed by any means other than a real Paddle webhook (direct SQL, a future admin tool, data migration, etc.), also reset credit in the same operation:

1. Look up the new plan's real allotment in `PLAN_BASE_CREDIT_USD` (`github-app/app_server/llm_cost.py`) — currently `flash: 5.00`, `air: 18.00`. Add `3.00` (`EXTRA_SEAT_LLM_CAP_USD`) per extra seat if the installation has any (`get_extra_seats`).
2. Set both `base_credit_allotment_usd` and `base_credit_remaining_usd` to that value. Do not leave one stale while updating the other — `release_llm_spend_reservation` relies on `base_credit_allotment_usd` being the real ceiling for this billing period to know how much of a later release should spill into `base_credit_remaining_usd` versus the never-expiring `topup_credit_balance_usd`.
3. Leave `topup_credit_balance_usd` untouched unless the change is also meant to grant or revoke a top-up credit specifically.
4. Health-check target eligibility needs no separate action — it re-evaluates plan live on every sweep tick.
5. If the account should also go through a real monthly reset cycle going forward, this manual change alone won't arrange that (`next_monthly_credit_reset_at` is only ever set by the annual-AIR Paddle path) — for a normal monthly-billed plan this doesn't matter, since Paddle's own webhook will resume driving future real changes.

## Why This Isn't A Code Bug

The Paddle-driven reset path (`reset_billing_period_credit` in `github-app/app_server/db.py`) was read in full and confirmed correct: it resets both credit fields to the new plan's real allotment whenever a genuine, newer billing period arrives, and its `IS NULL OR current_billing_period_start < $3` guard correctly protects against Paddle's out-of-order webhook delivery. A real customer downgrading through the normal Paddle flow gets this correctly. The gap is specifically that no code path exists for a *manual* plan change to trigger the same reset — which is expected, since a direct database write inherently bypasses whatever code would normally run. This procedure exists so that gap doesn't silently reproduce the same symptom (Flash Review quietly no-op'ing with no error) the next time a manual change is needed.
