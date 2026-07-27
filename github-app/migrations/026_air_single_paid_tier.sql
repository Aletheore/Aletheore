-- Aletheore now has a single paid tier (Aletheore AIR, internal plan
-- string "air") instead of indie/team/enterprise - any installation
-- still on one of those old plan strings would otherwise silently lose
-- its seat/health-target limits and spend cap (INCLUDED_SEATS,
-- INCLUDED_HEALTH_CHECK_TARGETS, and PLAN_MONTHLY_PRICE_USD no longer
-- have entries for them) the moment this migration's application code
-- deploys.
UPDATE installations SET plan = 'air' WHERE plan IN ('indie', 'team', 'enterprise');
