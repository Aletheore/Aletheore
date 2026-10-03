-- reverse_commission used to be all-or-nothing: any refund or chargeback
-- adjustment on a commissioned transaction, full or partial, zeroed the
-- entire commission. A small prorated credit-note adjustment on a renewal
-- shouldn't cost the affiliate their whole commission on that renewal.
--
-- charged_total_minor (the original transaction's own totals.total, same
-- shape as processed_paddle_transactions.charged_total_minor) lets the
-- reversal be prorated the same way claw_back_topup_credit already prorates
-- a top-up refund: share = refunded_total_minor / charged_total_minor.
-- Rows written before this migration keep NULL here and fall back to a full
-- reversal on their first adjustment, same as the old behavior - there is no
-- original-currency total to prorate against for them.
ALTER TABLE affiliate_commissions
    ADD COLUMN IF NOT EXISTS charged_total_minor NUMERIC(20, 0),
    ADD COLUMN IF NOT EXISTS reversed_usd NUMERIC(10, 2) NOT NULL DEFAULT 0;

-- One row per refund/chargeback adjustment already applied to a commission,
-- so the same adjustment delivered twice (created and updated, or a Paddle
-- retry) prorates the commission once, not twice - same shape and reason as
-- paddle_topup_adjustments.
CREATE TABLE IF NOT EXISTS affiliate_commission_adjustments (
    adjustment_id   TEXT PRIMARY KEY,
    transaction_id  TEXT NOT NULL REFERENCES affiliate_commissions(paddle_transaction_id) ON DELETE CASCADE,
    reversed_usd    NUMERIC(10, 2) NOT NULL DEFAULT 0,
    applied_at      TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS affiliate_commission_adjustments_transaction_id
    ON affiliate_commission_adjustments (transaction_id);
