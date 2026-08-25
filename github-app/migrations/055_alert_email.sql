-- A second alert delivery channel for endpoint monitoring, alongside the
-- existing installations.webhook_url (Slack/Teams). Installation-level,
-- not per-target, matching webhook_url's own scope - one configured
-- address covers every health check target on the installation.
ALTER TABLE installations ADD COLUMN IF NOT EXISTS alert_email TEXT;
