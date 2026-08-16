-- NOT NULL DEFAULT now() already stamps every existing row at migration
-- time, so there is never a NULL claimed_at afterward - no backfill needed.
ALTER TABLE webhook_deliveries
    ADD COLUMN IF NOT EXISTS claimed_at TIMESTAMPTZ NOT NULL DEFAULT now();

ALTER TABLE affiliate_commissions
    ADD COLUMN IF NOT EXISTS reversed BOOLEAN NOT NULL DEFAULT false;
