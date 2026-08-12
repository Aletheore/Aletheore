-- Endpoint checks are written every sweep. Keep enough history for the
-- public 7-day uptime view and authenticated trends without unbounded growth.
CREATE INDEX IF NOT EXISTS endpoint_health_retention_idx
    ON endpoint_health (checked_at);
