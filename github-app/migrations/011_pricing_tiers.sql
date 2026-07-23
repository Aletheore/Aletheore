-- Replaces the single flat "pro" plan with three tiers (starter/team/
-- enterprise) that carry different seat caps and different LLM tiers for
-- managed audits, Flash reviews, and AIRview builds. Existing "pro"
-- installations move to "enterprise" so nobody already paying loses
-- access to a feature or model tier they had before this ships.
-- Naturally idempotent: after the first run no row has plan = 'pro'.
UPDATE installations SET plan = 'enterprise' WHERE plan = 'pro';
