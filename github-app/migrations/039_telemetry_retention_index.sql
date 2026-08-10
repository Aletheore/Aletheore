-- Supports the telemetry retention sweep's range delete.
--
-- 023_cli_telemetry.sql indexes (event_type, occurred_at DESC), which serves
-- count_telemetry_events but cannot serve the sweep: that filters on
-- occurred_at alone, and Postgres can't range-scan the second column of a
-- composite index without a predicate on the first. Without this the sweep
-- degrades to a sequential scan over the one table an unauthenticated caller
-- can grow.
CREATE INDEX IF NOT EXISTS cli_telemetry_events_occurred_at_idx
    ON cli_telemetry_events (occurred_at);
