-- Purges dismissed_findings rows for secrets that were dismissed before the
-- CLI-3 fix (match_preview switched from four real leading/trailing
-- characters of the credential to a salted sha256:<12 hex> preview - see
-- src/aletheore/secrets.py's _redact).
--
-- identity_key for a secret dismissal is "path\x1fpattern\x1fmatch_preview"
-- (see app_server/dismissed_findings.py's finding_identity_key). Every
-- finding produced by the scanner now carries a sha256:-prefixed preview, so
-- a dismissal whose identity_key doesn't end in that shape can never again
-- match a freshly-computed one - it is not "possibly stale", it is
-- permanently unreachable dead weight. Unlike the CLI's own accepted_secrets
-- baseline (which re-derives the real value from the file every scan and can
-- dual-check old and new preview formats), the server never receives the raw
-- secret value, only the already-redacted preview - there is no legacy
-- format to recompute here, so there is nothing to migrate forward. Deleting
-- these rows doesn't lose anything that migrating could have saved; the
-- affected findings simply need dismissing again, once, the same as any
-- secret this installation has never seen before.
--
-- chr(31) is \x1f, the unit-separator field delimiter.
DELETE FROM dismissed_findings
WHERE finding_type = 'secret'
  AND split_part(identity_key, chr(31), 3) NOT LIKE 'sha256:%';
