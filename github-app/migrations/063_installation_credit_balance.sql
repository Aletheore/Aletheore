-- Real per-installation dollar-credit balance, replacing the flat
-- PLAN_CAP_OVERRIDE_USD ceiling. base_credit_remaining_usd resets every
-- billing-period renewal to PLAN_BASE_CREDIT_USD[plan] + the existing
-- per-seat bonus; topup_credit_balance_usd is a never-expiring balance
-- from customer-purchased credit, drawn down only after base is
-- exhausted. balance_epoch increments on every renewal reset AND every
-- top-up purchase - the "high-water mark" the low-balance/exhausted
-- email dedupe keys off of, via the existing sent_emails/dedupe_key
-- mechanism rather than new timestamp columns.
ALTER TABLE installations ADD COLUMN IF NOT EXISTS base_credit_remaining_usd NUMERIC NOT NULL DEFAULT 0;
ALTER TABLE installations ADD COLUMN IF NOT EXISTS topup_credit_balance_usd  NUMERIC NOT NULL DEFAULT 0;
ALTER TABLE installations ADD COLUMN IF NOT EXISTS current_billing_period_start TIMESTAMPTZ;
ALTER TABLE installations ADD COLUMN IF NOT EXISTS balance_epoch INTEGER NOT NULL DEFAULT 0;

-- Idempotency ledger for top-up purchases - Paddle retries a
-- transaction.completed it didn't get a 2xx for, with the same
-- transaction id. Recording every id this handler has already applied
-- prevents a retry from crediting the same purchase twice.
CREATE TABLE IF NOT EXISTS processed_paddle_transactions (
    id           TEXT PRIMARY KEY,
    processed_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- Backfill for installations that already existed when this migration
-- runs. Both credit columns default to 0, and the ONLY thing that ever
-- raises base_credit_remaining_usd is a Paddle subscription.updated
-- carrying a genuinely NEW current_billing_period.starts_at (see
-- reset_billing_period_credit in app_server/db.py). Without this,
-- every existing paid installation lands at a $0 balance on deploy and
-- is locked out of every AI feature until its next renewal webhook -
-- up to a full month, including this project's own live dogfooding
-- installations.
--
-- Amounts mirror app_server/llm_cost.py exactly: PLAN_BASE_CREDIT_USD
-- ({"flash": 5.00, "air": 18.00}) plus EXTRA_SEAT_LLM_CAP_USD (3.00)
-- per extra seat - i.e. base_credit_for_plan(plan, extra_seats),
-- computed inline here because a migration can't call Python.
-- extra_seats is a real column on installations (see get_extra_seats /
-- set_extra_seats in app_server/db.py and scan_worker/db.py), so the
-- seat bonus needs no join or subquery.
--
-- Guarded on "never been through a real renewal reset yet"
-- (current_billing_period_start IS NULL, which is true for exactly the
-- pre-existing population this migration is adding the column to) and
-- on a still-untouched zero balance, so a re-execution of this file
-- against an already-migrated database (see scripts/migrate.py's note
-- on docker-entrypoint-initdb.d) can never double-credit anyone.
UPDATE installations
SET base_credit_remaining_usd = CASE plan
        WHEN 'flash' THEN 5.00
        WHEN 'air' THEN 18.00
        ELSE 0
    END + 3.00 * COALESCE(extra_seats, 0)
WHERE plan IN ('flash', 'air')
    AND current_billing_period_start IS NULL
    AND base_credit_remaining_usd = 0;
