-- demo_scan_rate_limits (019_demo_scan_rate_limits.sql) backed the public,
-- unauthenticated "paste a repo" website demo, which has been removed
-- entirely (the CLI is free, and the demo's own sandboxed-container infra
-- and public attack surface weren't earning their cost against zero
-- external paying customers). No FK references this table and it held
-- nothing but ephemeral IP+timestamp rate-limit state, never real
-- customer or business data - safe to drop outright rather than archive.
DROP TABLE IF EXISTS demo_scan_rate_limits;
