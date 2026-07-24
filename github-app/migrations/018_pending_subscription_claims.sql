CREATE TABLE IF NOT EXISTS pending_subscription_claims (
    id BIGSERIAL PRIMARY KEY,
    claim_token TEXT NOT NULL UNIQUE,
    paddle_subscription_id TEXT NOT NULL UNIQUE,
    paddle_customer_id TEXT NOT NULL,
    paddle_customer_email TEXT,
    plan TEXT NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    claimed_at TIMESTAMPTZ,
    claimed_by_installation_id BIGINT REFERENCES installations(installation_id)
);

CREATE INDEX IF NOT EXISTS pending_subscription_claims_token
ON pending_subscription_claims (claim_token);

ALTER TABLE installations ADD COLUMN IF NOT EXISTS paddle_subscription_id TEXT;
ALTER TABLE installations ADD COLUMN IF NOT EXISTS paddle_customer_id TEXT;
