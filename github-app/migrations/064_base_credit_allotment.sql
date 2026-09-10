-- Tracks each installation's current billing-period base allotment,
-- separately from base_credit_remaining_usd (which draws down as
-- credit is spent). release_llm_spend_reservation needs this to know
-- how much "room" exists in base before a true-up release should spill
-- into topup_credit_balance_usd - without it, every release credited
-- 100% to the never-expiring topup bucket, so a customer's monthly
-- use-it-or-lose-it allotment never actually reset, it just kept
-- accumulating into permanent credit. Set alongside
-- base_credit_remaining_usd everywhere that column is set: a renewal
-- reset (reset_billing_period_credit) and a mid-cycle seat purchase
-- (credit_extra_seat_purchase).
ALTER TABLE installations ADD COLUMN IF NOT EXISTS base_credit_allotment_usd NUMERIC NOT NULL DEFAULT 0;

-- Same backfill population and guard as 063's base_credit_remaining_usd
-- backfill (pre-existing paid installations that have never been
-- through a real renewal reset yet), targeting this new column instead
-- so release_llm_spend_reservation has a correct ceiling for them
-- immediately, not just after their next renewal.
UPDATE installations
SET base_credit_allotment_usd = CASE plan
        WHEN 'flash' THEN 5.00
        WHEN 'air' THEN 18.00
        ELSE 0
    END + 3.00 * COALESCE(extra_seats, 0)
WHERE plan IN ('flash', 'air')
    AND current_billing_period_start IS NULL
    AND base_credit_allotment_usd = 0;
