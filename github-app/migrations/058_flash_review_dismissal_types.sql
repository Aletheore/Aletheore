-- dismissed_findings.finding_type was CHECK-constrained to ('secret',
-- 'vulnerability') only (see migration 033). Flash Review findings (both
-- LLM-generated and the free deterministic semantic checks) now reuse this
-- same table rather than a parallel dismissal system - see
-- app_server/dismissed_findings.py's finding_identity_key() for the two new
-- types' identity-key shape. Kept as two distinct types, not one generic
-- "flash_review", because a deterministic finding is pattern-matched
-- against real code (effectively proven) while an LLM finding is a model's
-- claim already passed through grounding - collapsing them would make it
-- impossible to later ask "do users dismiss deterministic findings at a
-- different rate than LLM ones", which is the whole reason this feedback
-- loop is being built.
--
-- The original CHECK was added inline in CREATE TABLE (migration 033) with
-- no explicit name, so Postgres auto-generated one. Looked up here via
-- information_schema rather than hardcoding the conventional
-- "dismissed_findings_finding_type_check" name - no live Postgres was
-- available to confirm that guess against this project's actual migration
-- history before shipping this, and a wrong hardcoded name fails loudly
-- (DROP CONSTRAINT errors if the name doesn't exist) rather than silently,
-- but there is no reason to guess when the real name can be found instead.
DO $$
DECLARE
    constraint_name text;
BEGIN
    SELECT con.conname INTO constraint_name
    FROM pg_constraint con
    JOIN pg_class rel ON rel.oid = con.conrelid
    WHERE rel.relname = 'dismissed_findings'
      AND con.contype = 'c'
      AND pg_get_constraintdef(con.oid) LIKE '%finding_type%';

    IF constraint_name IS NOT NULL THEN
        EXECUTE format('ALTER TABLE dismissed_findings DROP CONSTRAINT %I', constraint_name);
    END IF;

    ALTER TABLE dismissed_findings ADD CONSTRAINT dismissed_findings_finding_type_check
        CHECK (finding_type IN ('secret', 'vulnerability', 'flash_review_llm', 'flash_review_semantic'));
END $$;
