-- Real gap found auditing purge_installation_data (app_server/db.py):
-- its own docstring claims "Deleting the installations row cascades to
-- every installation-scoped table," but two tables' FKs to installations
-- did not actually cascade, and both store a real customer email address:
--
-- - sent_emails.installation_id was ON DELETE SET NULL (migration 030).
--   The row (including its `recipient` email address) survives an
--   installation purge with just the installation_id column nulled out -
--   record_sent_email's own dedupe_key is looked up on its own (see
--   email_already_sent), never joined against installation_id, so keeping
--   the row around after the installation is gone serves no functional
--   purpose at all, only leaks PII past what "erase everything" promises.
--
-- - pending_subscription_claims.claimed_by_installation_id had no ON
--   DELETE clause at all (defaults to NO ACTION, migration 018) - a
--   referencing row would make DELETE FROM installations fail outright
--   with a ForeignKeyViolation instead of purging, and the table's own
--   paddle_customer_email column is the same class of PII. This table has
--   no other code reference anywhere in the codebase today (the "claim a
--   subscription bought before installing the App" flow it backed appears
--   to have been superseded) - fixed defensively anyway, since a purge
--   crashing on a legacy row from before that code was removed is exactly
--   the kind of silent landmine this migration exists to close, and CASCADE
--   costs nothing on an otherwise-dead table.
--
-- Both already have the Postgres-default auto-generated constraint name
-- for an unnamed inline REFERENCES clause - dropped and re-added under the
-- same name so this is safe to run more than once (this project's own test
-- fixture applies every migration file on every run - see
-- github-app/tests/conftest.py's `pool` fixture).
ALTER TABLE sent_emails
    DROP CONSTRAINT IF EXISTS sent_emails_installation_id_fkey;
ALTER TABLE sent_emails
    ADD CONSTRAINT sent_emails_installation_id_fkey
    FOREIGN KEY (installation_id) REFERENCES installations(installation_id) ON DELETE CASCADE;

ALTER TABLE pending_subscription_claims
    DROP CONSTRAINT IF EXISTS pending_subscription_claims_claimed_by_installation_id_fkey;
ALTER TABLE pending_subscription_claims
    ADD CONSTRAINT pending_subscription_claims_claimed_by_installation_id_fkey
    FOREIGN KEY (claimed_by_installation_id) REFERENCES installations(installation_id) ON DELETE CASCADE;
