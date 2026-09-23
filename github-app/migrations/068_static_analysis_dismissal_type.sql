-- dismissed_findings.finding_type was CHECK-constrained to ('secret',
-- 'vulnerability', 'flash_review_llm', 'flash_review_semantic') (see
-- migration 058). Static-analysis findings (SonarQube/Semgrep/Bearer/
-- gosec/Bandit/Joern, normalized into security.static_analysis) now reuse
-- this same table rather than a parallel dismissal system - see
-- app_server/dismissed_findings.py's finding_identity_key() for this new
-- type's identity-key shape ((path, line, tool, rule_id), a stable
-- structured tuple - unlike Flash Review's free-text issue field, a
-- deterministic scanner's rule_id doesn't get reworded between runs, so
-- this doesn't need the fuzzy-fingerprint treatment flash_review_llm/
-- flash_review_semantic use).
--
-- Same reasoning as migration 058 for looking the constraint name up
-- rather than hardcoding it: no live Postgres was available to confirm a
-- guessed name against this project's actual migration history before
-- shipping this.
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
        CHECK (finding_type IN ('secret', 'vulnerability', 'flash_review_llm', 'flash_review_semantic', 'static_analysis'));
END $$;
