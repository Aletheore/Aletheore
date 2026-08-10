-- GET /v1/health/{org}/{repo} (dashboard.py) was unauthenticated,
-- CORS *, and gated on nothing but "does this repo have any health-check
-- data" - any repo a customer monitors was publicly exposed (endpoint
-- paths, status, latency) by default, with no way to turn it off.
-- Defaults to false: existing installations must explicitly opt in.
ALTER TABLE installations
    ADD COLUMN IF NOT EXISTS public_status_enabled BOOLEAN NOT NULL DEFAULT false;
