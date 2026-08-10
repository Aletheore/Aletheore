-- installations.account_login was a per-request lookup key
-- (_repo_installation_id and now the Marketplace webhook fix) with no
-- uniqueness guarantee - an unordered fetchrow against a duplicate could
-- pick either row nondeterministically. GitHub account/org names are also
-- reusable after deletion, so a login collision was possible even
-- without a bug. Confirmed no existing duplicates in production before
-- this migration was written (SELECT account_login, count(*) ... HAVING
-- count(*) > 1 returned 0 rows).
CREATE UNIQUE INDEX IF NOT EXISTS installations_account_login_unique
    ON installations (account_login);
