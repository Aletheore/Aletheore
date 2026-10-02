-- Record what each top-up granted so a refund or chargeback can take the same
-- credit back. Until now the ledger held only the Paddle transaction id, which
-- was enough to credit once but not to reverse anything.
--
-- installation_id references installations with ON DELETE CASCADE, so the
-- ledger rows go with the account on deletion like every other
-- installation-scoped table. Rows written before this migration keep NULLs
-- in the new columns and are reported for manual follow-up if refunded.
ALTER TABLE processed_paddle_transactions
    ADD COLUMN IF NOT EXISTS installation_id BIGINT REFERENCES installations(installation_id) ON DELETE CASCADE,
    ADD COLUMN IF NOT EXISTS credited_usd NUMERIC(14, 4),
    ADD COLUMN IF NOT EXISTS charged_total_minor NUMERIC(20, 0),
    ADD COLUMN IF NOT EXISTS clawed_back_usd NUMERIC(14, 4) NOT NULL DEFAULT 0;

-- One row per refund or chargeback adjustment already applied, so the same
-- adjustment delivered twice (created and updated, or a Paddle retry) takes
-- credit back once. shortfall_usd is the part that could not be taken back
-- because the buyer had already spent it.
CREATE TABLE IF NOT EXISTS paddle_topup_adjustments (
    adjustment_id   TEXT PRIMARY KEY,
    transaction_id  TEXT NOT NULL REFERENCES processed_paddle_transactions(id) ON DELETE CASCADE,
    clawed_back_usd NUMERIC(14, 4) NOT NULL DEFAULT 0,
    shortfall_usd   NUMERIC(14, 4) NOT NULL DEFAULT 0,
    applied_at      TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS paddle_topup_adjustments_transaction_id
    ON paddle_topup_adjustments (transaction_id);
