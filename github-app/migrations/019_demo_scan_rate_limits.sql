-- Per-IP cooldown for the public, unauthenticated "paste a repo" website
-- demo. Unlike managed_audit_rate_limits, this has no installation to key
-- on - the caller is an anonymous visitor identified only by IP.
CREATE TABLE IF NOT EXISTS demo_scan_rate_limits (
    client_ip    TEXT NOT NULL PRIMARY KEY,
    last_run_at  TIMESTAMPTZ NOT NULL
);
