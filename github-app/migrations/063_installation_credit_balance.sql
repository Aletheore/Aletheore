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
