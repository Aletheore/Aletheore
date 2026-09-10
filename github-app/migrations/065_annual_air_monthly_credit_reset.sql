-- Only an ANNUAL subscriber needs this: base_credit_remaining_usd/
-- base_credit_allotment_usd otherwise only reset via
-- reset_billing_period_credit, gated on Paddle's current_billing_period.
-- starts_at genuinely changing - which for an annual subscription only
-- happens once a year, so an annual AIR customer's intended MONTHLY $18
-- allotment (see PLAN_BASE_CREDIT_USD in app_server/llm_cost.py) would
-- otherwise land once for the whole year instead of 12 times. NULL for
-- every monthly subscriber and every non-air/free installation - the
-- scheduled sweep (scan_worker/jobs.py's run_monthly_credit_reset_sweep_job)
-- only ever looks at rows where this is NOT NULL and due.
ALTER TABLE installations ADD COLUMN IF NOT EXISTS next_monthly_credit_reset_at TIMESTAMPTZ;

-- No backfill, unlike 063/064: there are zero live annual subscribers to
-- backfill (("air", "year") in app_server/paddle_pricing.py's
-- PLAN_INTERVAL_TO_PRICE_ID is a brand new price), and the webhook handler
-- populates this column for every annual subscriber on their next real
-- subscription.updated anyway - the same "correct from here forward"
-- guarantee base_credit_allotment_usd relied on for fresh resets.

-- The sweep's due-list query is WHERE next_monthly_credit_reset_at IS NOT
-- NULL AND next_monthly_credit_reset_at <= now(), run on every ~3-minute
-- scheduler tick. A partial index keyed on that same column, holding only
-- the handful of annual subscribers (every other row is NULL and excluded
-- from the index entirely), keeps that tick a cheap index scan rather than
-- a full installations sequential scan forever.
CREATE INDEX IF NOT EXISTS installations_next_monthly_credit_reset_at
    ON installations (next_monthly_credit_reset_at)
    WHERE next_monthly_credit_reset_at IS NOT NULL;
