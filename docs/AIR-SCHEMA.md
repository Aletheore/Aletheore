# The AIR Contract

**Purpose:** Define AIR (`air.json`) as a versioned public contract and the rules for changing it.
**Status:** Active baseline
**Owner:** Arihant Kaul
**Related Documents:** [README.md](README.md), [../schemas/air.schema.json](../schemas/air.schema.json), [../SECURITY.md](../SECURITY.md)
**Last Updated:** 2026-08-09

## Purpose

AIR is the deterministic evidence document produced by `aletheore scan`. It is
read by the CLI, the MCP server, the hosted dashboard, the GitHub App worker,
and by customers writing their own tooling. That last group is why AIR is a
contract and not an implementation detail: a key rename that is trivial inside
this repository is a breaking change outside it.

## Where the contract lives

| Artifact | Role |
| --- | --- |
| `src/aletheore/air_schema.py` | The contract itself, as JSON Schema (draft 2020-12). Single source of truth. |
| `schemas/air.schema.json` | The same schema, checked in for external consumers to fetch. Generated, never hand-edited. |
| `EVIDENCE_VERSION` in `src/aletheore/evidence.py` | The version stamped into every scan. |
| `src/tests/test_air_schema.py` | Enforcement. Nothing below is advisory. |

## Versioning

`EVIDENCE_VERSION` is semver, currently pre-1.0. While major is `0`, a **MINOR**
bump signals the breaking change — `0.2.0` and `0.2.7` are the same schema,
`0.2.0` and `0.3.0` are not. `is_evidence_version_compatible` compares
`(major, minor)`, so a reader refuses evidence from a different minor rather
than misreading it. Once AIR reaches `1.0.0`, that comparison moves to major
alone.

Version `0.4.0` adds `git.file_ownership`, a current-source-file keyed
ownership breakdown used by file-scoped ownership queries.

Version `0.5.0` adds `repository.modules[].import_confidence`, an optional
per-module map from a resolved import target to `"inferred"` or
`"ambiguous"` - see the changelog entry below.

## Migration rules

1. **Any change to `AIR_JSON_SCHEMA` requires an `EVIDENCE_VERSION` MINOR bump.**
   This includes adding a key. Additive changes feel safe, but a consumer that
   version-checks and then reads the new key would silently get `KeyError` on
   older evidence that passed the check.
2. **Update all four artifacts together** in one change: the schema module, the
   regenerated `schemas/air.schema.json`, `EVIDENCE_VERSION`, and the
   `EXPECTED_SCHEMA_FINGERPRINT` / `EXPECTED_EVIDENCE_VERSION` constants in
   `test_air_schema.py`.
3. **Never update the fingerprint alone.** The fingerprint exists to make step 1
   impossible to skip. Changing it to make a red test green defeats the only
   mechanism preventing silent drift.
4. **Record the change** in the changelog below.
5. **Do not narrow an array's item schema to match a detector's current output.**
   Item schemas deliberately require only the fields consumers read; detectors
   stay free to add their own keys without a version bump.

Regenerate the published schema with:

```bash
cd src && python3 -c "import json, pathlib; from aletheore.air_schema import AIR_JSON_SCHEMA; \
pathlib.Path('../schemas/air.schema.json').write_text(json.dumps(AIR_JSON_SCHEMA, indent=2, sort_keys=True) + '\n')"
```

## What is enforced, and where

- **Producer conformance.** `test_scan_output_conforms_to_the_schema` runs a real
  scan of a real git repo and validates the result with `deep=True`. The schema
  is a claim about what `scan_repository` emits, so it is checked against actual
  output rather than a fixture.
- **Consumer coverage.** `test_schema_covers_every_path_consumers_index` asserts
  the reverse: every two-level path the CLI, MCP server, and dashboard index
  into is declared. A schema that omitted a key consumers depend on would pass
  conformance while protecting nothing.
- **Drift.** `test_schema_changes_require_an_evidence_version_bump` pins the
  fingerprint.
- **Read boundary.** `load_evidence_file` validates shape after the version
  check and raises `MalformedEvidenceError` naming the offending path. A
  truncated, hand-edited, or foreign file now fails at the boundary instead of
  as a `KeyError` several modules downstream.

Validation at the read boundary is shallow (`deep=False`): it checks the
section skeleton and container types, which is what a consumer about to index
into evidence actually depends on, and stays O(sections) regardless of repo
size. Deep validation walks every element of every typed array — tens of
thousands of objects on a large repo — and is reserved for the test suite.

## Conditional sections

`git` is the one section whose contents are conditional. A repository with no
commits yields `{"available": false}` and nothing else, so `available` is its
only required key. **Consumers must branch on `git.available` before reading
anything else in that section.** `git.hotspots` is likewise present only when
git is available and modules were found.

`architecture.config_applied` is `null` whenever the repository ships no
architecture config, which is the common case.

## Changelog

| Version | Change |
| --- | --- |
| `0.5.0` | Added `repository.modules[].import_confidence` (optional): a map from a resolved import target already present in that module's own `imports` to `"inferred"` (a source-root/namespace-prefix/PSR-4-prefix tiebreak among genuinely multiple candidates picked a winner) or `"ambiguous"` (C# only - a type-reference edge kept despite more than one file declaring that type name, rather than the previous behavior of dropping it outright). Omitted for a module with no non-exact edges - every module in six of the eleven supported languages (js/ts, go, rust, ruby, c/cpp), whose resolvers are never ambiguous, plus the common single-candidate case in the other five. Also fixed a real non-determinism bug found while adding this: Java's multi-source-root tiebreak previously picked whichever root came first in filesystem walk order (not guaranteed stable across runs/platforms), now sorted the same shallowest-first way Python's own source roots already were. |
| `0.3.0` | Added `repository.database.schema`: tables, columns, foreign-key relations, and indexes replayed from Postgres DDL migrations, each citing the file:line that introduced it. Gated — present with `checked: false` when the installation is not entitled, so the section's keys are identical for every user and only `checked` varies. |
| `0.2.0` | Contract formalized: JSON Schema extracted, published, and enforced in CI. No shape change from the `0.2.0` documents already in the wild — this version documents the existing shape rather than altering it. |
