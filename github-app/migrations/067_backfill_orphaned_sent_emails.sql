-- Backfill for the gap migration 066 fixed the FK for, but didn't backfill:
-- before 066, sent_emails.installation_id was ON DELETE SET NULL, so every
-- purge_installation_data run before 066 shipped left the row itself -
-- including its real recipient email address - sitting in the table with
-- just installation_id nulled out. 066's ON DELETE CASCADE only prevents
-- this going forward; it does nothing for rows already orphaned by then.
-- Auditing purge_installation_data's real completeness (not just its
-- code) found these still present today - the exact PII leak "erase
-- everything" promises not to leave behind.
--
-- installation_id IS NULL is not on its own proof of orphaning: the
-- "welcome" email (sent at first sign-in, before a user has necessarily
-- connected any installation - see app_server/auth.py) is the one
-- template that legitimately inserts a NULL installation_id by design.
-- Every other registered template (payment_failed, subscription_canceled,
-- weekly_digest, health_alert, credit_low_balance, credit_exhausted - see
-- scan_worker/jobs.py's _EMAIL_TEMPLATES) is installation-scoped and its
-- one enqueue_transactional_email call site always passes a real
-- installation_id - so a NULL installation_id on any of THOSE rows can
-- only be the pre-066 SET-NULL orphaning this migration exists to clean
-- up, never a legitimate insert. Excluding 'welcome' is what keeps this
-- migration from deleting real, still-relevant records under the same
-- backfill sweep.
-- Flash Review flagged this as an unguarded, irreversible DELETE resting
-- entirely on the comment above's claim about auth.py/jobs.py, which this
-- diff can't itself verify. Real safety net, not just documentation: abort
-- instead of deleting if the matched-row count is wildly outside what a
-- one-time legacy backfill on this project's real (~10k install) scale
-- should ever look like - a real signal the "only welcome is NULL"
-- assumption doesn't hold today, not something to find out after the
-- rows are already gone. 10000 is a sanity ceiling, not a business rule;
-- bump it with a fresh comment if a real audit justifies a higher one.
--
-- Delete and count are the same statement (DELETE ... RETURNING feeding
-- the COUNT), not a separate SELECT COUNT followed by a separate DELETE -
-- a self-review round on this exact fix caught that the two-statement
-- version had a real TOCTOU gap under READ COMMITTED (a row written
-- between the two statements would miss the guard's count but still be
-- deleted). RAISE EXCEPTION here rolls back the whole DO block, so an
-- over-threshold count undoes the delete too, not just skips a step
-- after it already happened.
DO $$
DECLARE
    orphan_count integer;
BEGIN
    WITH deleted AS (
        DELETE FROM sent_emails
        WHERE installation_id IS NULL
          AND template_name != 'welcome'
        RETURNING 1
    )
    SELECT COUNT(*) INTO orphan_count FROM deleted;

    IF orphan_count > 10000 THEN
        RAISE EXCEPTION
            'sent_emails orphan-row count (%) exceeds the expected one-time backfill scope (10000) - aborting (delete rolled back) for manual review instead of risking an unintended mass delete',
            orphan_count;
    END IF;

    RAISE NOTICE 'deleted % orphaned sent_emails row(s) (NULL installation_id, non-welcome template)', orphan_count;
END $$;
