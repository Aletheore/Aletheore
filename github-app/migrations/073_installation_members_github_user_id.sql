-- installation_members is keyed on github_login, a mutable string - if a
-- member's GitHub username changes, or is abandoned and later claimed by a
-- different GitHub account, authorization (is_installation_member) has no
-- way to tell the new holder of that login apart from the original member
-- it was actually granted to. Same anti-pattern as the already-fixed
-- stale-credential-access-after-member-offboarding finding, in this
-- subsystem.
--
-- Nullable and not backfillable from here: resolving an existing row's
-- login to its real numeric GitHub user id needs a live GitHub API call,
-- which a migration can't make. auth.py's OAuth callback binds this lazily
-- instead, the first time that login's holder actually logs in after this
-- migration runs - see bind_installation_member_identity.
ALTER TABLE installation_members
    ADD COLUMN IF NOT EXISTS github_user_id BIGINT;
