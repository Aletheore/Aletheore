-- Durable per-event cost ledger. llm_spend itself only ever stored one
-- blended monthly total per installation, with no feature breakdown - the
-- only place a feature label ("flash_review", "airview_incremental", etc.)
-- was ever attached to a cost was a log line, and logs don't survive a
-- container restart (every deploy wipes them). Found via a real audit: a
-- $8.29/8-day spend on one installation was completely unexplainable after
-- the fact because nothing durable recorded which feature spent it. This
-- table is written alongside the existing aggregate on every
-- record_llm_spend call, so "where did the money go" is answerable from
-- the database, not from logs that may already be gone.
CREATE TABLE IF NOT EXISTS llm_spend_events (
    id                BIGSERIAL PRIMARY KEY,
    installation_id   BIGINT NOT NULL REFERENCES installations(installation_id) ON DELETE CASCADE,
    feature           TEXT NOT NULL,
    cost_usd          NUMERIC NOT NULL,
    created_at        TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS llm_spend_events_lookup
ON llm_spend_events (installation_id, created_at);

CREATE INDEX IF NOT EXISTS llm_spend_events_feature_lookup
ON llm_spend_events (installation_id, feature, created_at);
