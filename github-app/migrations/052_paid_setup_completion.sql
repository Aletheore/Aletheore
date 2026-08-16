-- Tracks whether the one-time paid-plan setup (initial Live Wiki/Docs
-- build, affiliate attribution) has actually run, separately from the
-- installation's current plan. A crash between the free->paid plan write
-- and this setup running left plan already paid on Paddle's retry, so a
-- gate derived from "did the plan just transition" silently and
-- permanently skipped setup forever on retry.
--
-- Defaults to now() ("nothing pending") for every row, existing or new -
-- an installation that is already paid (migrated data, a direct insert,
-- a test fixture) was never a fresh transition this code observed, so it
-- must not be treated as owing a first-time setup run. claim_free_to_paid_plan
-- resets this to NULL as part of the same atomic UPDATE the moment it
-- detects a genuine free->paid transition, which is the only place setup
-- should ever become "pending" again.
-- Nullable, not NOT NULL: claim_free_to_paid_plan explicitly resets this
-- back to NULL to mark setup pending again, so the column must be able to
-- hold NULL - only the DEFAULT (applied when a row is inserted without
-- naming this column at all) is now().
ALTER TABLE installations
    ADD COLUMN IF NOT EXISTS paid_setup_completed_at TIMESTAMPTZ DEFAULT now();
