# Aletheore License-Detection Benchmark

`src/aletheore/licenses.py` (`categorize_license`, `detect_repo_license`,
`check_dependency_licenses`) already had 46 unit tests, all mocking
registry responses — thorough for the fetch/cache/concurrency/timeout
machinery, but every license *text* fixture in those tests was
hand-written to obviously contain a recognizable keyword. Like
`ast-pattern-benchmark`, this ran the real functions against real repos
instead of a new synthetic corpus, specifically to find text the
existing fixtures wouldn't think to write.

## Result: 3 real gaps found and fixed, all confirmed against real repos

### 1. BSD license body text contains no "bsd" keyword at all

Neither the 2-clause nor 3-clause BSD license *body* text contains the
literal word "BSD" anywhere — it's purely descriptive redistribution
terms with no self-identifying name. Confirmed present verbatim (the
canonical "Redistribution and use in source and binary forms..." opening
line) in 4 real repos checked: click, flask, django, gorilla/mux. Two of
those (flask, gorilla/mux) have no machine-readable license metadata
(no `pyproject.toml`/`package.json` license field) and rely purely on
the LICENSE file body — both categorized **"unknown" despite being
unambiguously, famously BSD-licensed real projects**.

**Fix**: added the canonical BSD opening phrase itself as a permissive
marker.

### 2. `LICENSE.rst` missing from the checked filename list

Flask's own real repo uses this exact filename (a common convention for
reStructuredText-docs-style Python projects) — invisible to
`detect_repo_license`'s filename list entirely, independent of gap #1.

**Fix**: added `LICENSE.rst` alongside the existing `LICENSE`/
`LICENSE.md`/`LICENSE.txt`/`COPYING`.

### 3. Maven license lookup never followed `<parent>` POM references

A very common real Maven convention: the `<licenses>` block lives on a
shared parent POM, not the artifact's own. Confirmed directly: Guava's
real `guava-33.6.0-jre.pom` has no `<licenses>` element at all (only a
`<parent>` reference to `guava-parent`) — its own pom.xml even has a
comment explaining why ("copied from the parent pom because..."). A
single-level fetch (the pre-fix behavior) always returned `None` for
Guava, Guava-testlib, and Protobuf-java — three real, unambiguously
Apache-2.0/BSD-licensed artifacts found via `gson`'s own real
`pom.xml`, all three came back "unknown".

**Fix**: `_fetch_maven_license` now follows `<parent>` references up to
3 hops (real chains are 1-2 levels; bounded against an unexpected cycle
or unusually deep chain), re-verified: Guava now resolves to "Apache
License, Version 2.0", Protobuf-java to "BSD-3-Clause".

### Bonus: CDDL added to the weak-copyleft bucket

While re-checking `gson`'s dependencies post-fix, one real, correctly-
fetched license (`javax.annotation:jsr250-api`'s "COMMON DEVELOPMENT AND
DISTRIBUTION LICENSE (CDDL) Version 1.0") fell through every existing
category to "unknown" — CDDL is a real, standard weak-copyleft license
family (same SPDX/OSI bucket as MPL/EPL), not an unrecognizable one.
Added alongside the existing weak-copyleft markers.

## Verification

Real end-to-end checks, not just the new unit tests: `detect_repo_license`
re-run against the exact flask/gorilla-mux checkouts that surfaced gaps
#1/#2 (both now `"category": "permissive"`), `_fetch_maven_license`
re-run against the exact Guava/Protobuf-java coordinates that surfaced
gap #3 (both now resolve correctly), full `check_dependency_licenses`
re-run against gson's real repo (findings dropped from 9 to 2 — 7 of the
original 9 were Guava/Guava-testlib/Protobuf-java/Protobuf-java-util/
Caliper/Truth-proto-extension all resolving through the same
guava-parent chain, now all correctly categorized "permissive" and no
longer findings at all; the 2 that remain are a `-SNAPSHOT` version with
no published Maven Central artifact to fetch at all - expected, not a
bug - and `jsr250-api`'s CDDL dependency, which is *correctly* still a
finding post-fix: its `category` changed from the wrong "unknown" to the
real "copyleft-weak", which the findings list is supposed to surface).
Full suite: 1591 passed (46 pre-existing + 5 new `test_licenses.py`
tests, all passing).
