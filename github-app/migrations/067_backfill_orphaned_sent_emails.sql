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
DELETE FROM sent_emails
WHERE installation_id IS NULL
  AND template_name != 'welcome';
