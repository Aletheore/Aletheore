-- Anonymous CLI usage telemetry: how many times (and from how many
-- distinct machines) `aletheore scan` actually gets run. No installation,
-- repo name, or code content is ever included - anonymous_id is a random
-- UUID generated once per machine and cached locally (see
-- aletheore/telemetry.py), not tied to any account. Reporting respects
-- ALETHEORE_TELEMETRY_DISABLED/DO_NOT_TRACK and fails silently if the
-- network call doesn't succeed - this table is a marketing/usage signal,
-- never something the CLI's actual behavior depends on.
CREATE TABLE IF NOT EXISTS cli_telemetry_events (
    id              BIGSERIAL PRIMARY KEY,
    event_type      TEXT NOT NULL,
    anonymous_id    TEXT NOT NULL,
    occurred_at     TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS cli_telemetry_events_lookup
ON cli_telemetry_events (event_type, occurred_at DESC);
