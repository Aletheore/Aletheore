-- Affiliate program: manually-onboarded creators earn 15% recurring
-- commission on customers referred via their own Paddle discount code. See
-- docs/superpowers/specs/2026-08-10-aletheore-affiliate-program-design.md.

CREATE TABLE IF NOT EXISTS affiliates (
    id                  BIGSERIAL PRIMARY KEY,
    code                TEXT NOT NULL UNIQUE,
    paddle_discount_id  TEXT NOT NULL UNIQUE,
    name                TEXT NOT NULL,
    created_at          TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- installation_id as the PRIMARY KEY (not just a unique column) makes
-- first-touch attribution an INSERT ... ON CONFLICT DO NOTHING - a second
-- referral for the same installation is a database-enforced no-op, not
-- something application code has to remember to check.
CREATE TABLE IF NOT EXISTS affiliate_referrals (
    installation_id  BIGINT PRIMARY KEY REFERENCES installations(installation_id) ON DELETE CASCADE,
    affiliate_id     BIGINT NOT NULL REFERENCES affiliates(id),
    referred_at      TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- paddle_transaction_id UNIQUE does the same job for transaction.completed
-- webhook retries (Paddle retries any non-2xx response, re-delivering the
-- same transaction id) - a repeat delivery can't double-count a commission.
CREATE TABLE IF NOT EXISTS affiliate_commissions (
    id                    BIGSERIAL PRIMARY KEY,
    affiliate_id          BIGINT NOT NULL REFERENCES affiliates(id),
    installation_id       BIGINT NOT NULL REFERENCES installations(installation_id) ON DELETE CASCADE,
    paddle_transaction_id TEXT NOT NULL UNIQUE,
    amount_usd            NUMERIC(10,2) NOT NULL,
    transaction_date      TIMESTAMPTZ NOT NULL,
    paid                  BOOLEAN NOT NULL DEFAULT false
);

CREATE INDEX IF NOT EXISTS affiliate_commissions_affiliate_id ON affiliate_commissions (affiliate_id);
