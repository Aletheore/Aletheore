# Aletheore Security-Scanner Benchmark — Results

Run date: 2026-09-03. First-ever systematic accuracy measurement for
`aletheore_secrets` and `aletheore_vulnerabilities` — the deterministic
scanners behind Aletheore's "evidence-backed" findings claim. Two rounds:
a synthetic pilot corpus (exact-match ground truth), and a real-repo
validation run (real, uncontrolled code) that found and drove fixes for
six real product gaps, all now shipped — including the one gap round 2
initially deferred as narrower/higher-risk, closed in a follow-up pass
once the rest of the sweep was done.

## Synthetic pilot corpus

| case_id | category | outcome |
|---|---|---|
| 001-aws-access-key | true_positive | TP |
| 002-github-token | true_positive | TP |
| 003-stripe-key | true_positive | TP |
| 004-slack-token | true_positive | TP |
| 005-google-api-key | true_positive | TP |
| 006-uuid-not-a-secret | true_negative | TN |
| 007-private-key-header | true_positive | TP |
| 008-test-tls-certificate | true_negative | TN |
| 009-property-reference-not-a-secret | true_negative | TN |
| 010-truncated-example-not-a-secret | true_negative | TN |
| 011-pem-header-boilerplate-no-body | true_negative | TN |

**secrets: recall 6/6, false positives 0/5.** Cases 007-011 were added
after the real-repo run below found real gaps in patterns the original
5-case pilot didn't exercise (`private_key_header`, and three new
`generic_credential_assignment`/`private_key_header` false-positive
shapes) — each new case encodes the exact real shape that was found, so
the pilot now has permanent regression coverage for every fix in this
report.

| case_id | ecosystem | category | outcome |
|---|---|---|---|
| 001-log4shell-maven | Maven (pom.xml) | true_positive | TP |
| 002-flask-pypi | PyPI | true_positive | TP |
| 003-jwt-go-algorithm-confusion | Go | true_positive | TP |
| 004-lodash-redos-npm | npm | true_positive | TP |
| 005-six-clean-pypi | PyPI | true_negative | TN |
| 006-time-crate-segfault-cratesio | crates.io | true_positive | TP |
| 007-clean-cratesio | crates.io | true_negative | TN |
| 008-rack-dos-rubygems | RubyGems | true_positive | TP |
| 009-clean-rubygems | RubyGems | true_negative | TN |
| 010-guzzle-psr7-header-injection-packagist | Packagist | true_positive | TP |
| 011-clean-packagist | Packagist | true_negative | TN |
| 012-newtonsoft-json-nuget | NuGet | true_positive | TP |
| 013-clean-nuget | NuGet | true_negative | TN |
| 014-jackson-databind-xxe-gradle | Maven (build.gradle) | true_positive | TP |
| 015-clean-gradle | Maven (build.gradle) | true_negative | TN |
| 016-swift-nio-crlf-injection-swift | SwiftURL | true_positive | TP |
| 017-clean-swift | SwiftURL | true_negative | TN |

**vulnerabilities: recall 10/10, false positives 0/7 — all 10 ecosystems
`vulnerabilities.py` parses now have a pilot case.** Cases 006-017 close
out the gap this report's original "Suggested next steps" flagged
(crates.io, RubyGems, Packagist, NuGet, Gradle, Swift never had a
hand-verified CVE case, only real-repo-run coverage). Every true-positive
case's CVE was independently confirmed against live OSV.dev at
corpus-build time (2026-09-03), same rigor as the original 4; case 014
deliberately uses Gradle's Groovy-DSL manifest format (not pom.xml) to
get real parser coverage distinct from case 001 despite sharing the same
underlying OSV "Maven" ecosystem. No gaps found — every ecosystem's
manifest parser produced the correct recall/false-positive result on the
first pass.

Both scanners run for real here, not mocked: `find_secrets()` scans
actual materialized fixture files, `check_vulnerabilities()` makes real,
live calls to OSV.dev — the same functions and API production uses.

**Caveats**: N is still small per ecosystem (1 true-positive + 1
true-negative case each, except PyPI/Maven with more) — enough to catch
a broken parser, not enough for a statistically meaningful per-ecosystem
claim on its own. The vulnerabilities arm's ground truth is OSV.dev
itself, the same source the scanner queries live — a clean pass mainly
confirms manifest-parsing/OSV-integration correctness, not independent
real-world accuracy. See `README.md`'s "Known limitations" for the full
list.

## Real-repo validation

The synthetic pilot controls every input, so it can't say anything about
false-positive rate on real, uncontrolled code. Two rounds of real-repo
scanning fixed that: 11 repos initially (reusing repos/commits already
vetted in `pr-review-benchmark/cases/*/repo.txt`), then 9 more added to
cover ecosystems and repo shapes the first round didn't touch (Rust,
Ruby, PHP, .NET, Gradle/Kotlin, Swift, plus 3 large diverse Python/Go/JS
repos for deeper secrets-FP-rate signal). All 20 repos downloaded to a
scratch directory (never committed here) and scanned directly with
`find_secrets()`/`check_vulnerabilities()`.

| repo | ecosystem | files | secrets findings | vuln findings |
|---|---|---|---|---|
| flask | PyPI | 242 | 0 | 25 |
| requests | PyPI | 92 | 0 | 0 |
| click | PyPI | 145 | 0 | 0 |
| express | npm | 213 | 0 | 4 |
| lodash | npm | 95 | 0 | 13 |
| axios | npm | 442 | 1 | 22 |
| cobra | Go | 65 | 0 | 0 |
| gin | Go | 127 | 1 | 51 |
| gorilla/mux | Go | 26 | 0 | 0 |
| gson | Maven | 312 | 0 | 0 |
| junit4 | Maven | 566 | 0 | 0 |
| clap | crates.io | 612 | 0 | 42 |
| sinatra | RubyGems | 289 | 2 | 30 |
| laravel | Packagist | 56 | 0 | 5 |
| restsharp | NuGet | 466 | 4 | 0 |
| okhttp | Gradle | 835 | 18 | 2 |
| penny-bot (vapor) | Swift | 244 | 17 | 0 |
| django | PyPI | 6,979 | 32 | 10 |
| client-go | Go | 2,531 | 20 | 2 |
| react | npm | 7,093 | 1 | 25 |
| **total** | | **21,430** | **96** | **231** |

Every one of the 96 secrets findings was manually reviewed (path,
surrounding code, and — critically — cross-checked against the actual
file content, never just the redacted `match_preview` hash). That review
found and fixed **six real product gaps**, all now shipped with
regression tests, re-verified against the exact real files that
surfaced them:

### Fix 1 — PEM test certificates never suppressed (found round 1)

`private_key_header`'s "value" is the fixed header line
(`-----BEGIN RSA PRIVATE KEY-----`), whose entropy (~3.38) sits just
above `_is_likely_placeholder`'s low-entropy threshold (3.0) regardless
of whether the key behind it is real or a test fixture — so a committed
test certificate at an unambiguously test-suggestive path was never
suppressed. Found in `axios/tests/unit/adapters/key.pem` and
`gin/testdata/certificate/key.pem`. **Fix**: for `private_key_header`
specifically, a test-suggestive path is now sufficient on its own —
entropy isn't a meaningful signal for this pattern either way. Tests:
`test_find_secrets_flags_a_test_tls_certificate_as_likely_placeholder`,
`test_find_secrets_does_not_downgrade_a_private_key_header_outside_a_test_path`.
Pilot case: `008-test-tls-certificate`.

### Fix 2 — bare property/variable references matched as literal secrets (found round 2)

`generic_credential_assignment`'s value class includes `.` to support
real dotted-key shapes (`self.PASSWORD=`, `cfg.API_KEY=`), but that same
allowance means `secret = obj.Attribute` — ordinary variable/property
reference code, never a hardcoded credential — matches too. Found
**independently in three unrelated codebases**: RestSharp's C# OAuth2
authenticators (`["client_secret"] = TokenRequest.ClientSecret`),
client-go's Go kubeconfig merging (`Password = configAuthInfo.Password`),
and Django's own Python `salted_hmac()` (`secret = settings.SECRET_KEY`)
— the repeated, independent appearance is what made this the
highest-confidence fix of the round. **Fix**: a value matching a bare
dotted-identifier-chain shape is now treated as a placeholder, gated by
a per-segment length cap (32 chars) tuned against the longest real
identifier segment found (`promptedCredentials`, 19 chars) and the
shortest real dotted-credential segment already covered by an existing
test (Google AI Studio's `AQ.<43-char random>` key format) — so the fix
can't swallow that real format. Tests:
`test_find_secrets_flags_a_property_reference_as_likely_placeholder`,
`test_find_secrets_does_not_flag_a_real_dotted_credential_value_as_a_property_reference`.
Pilot case: `009-property-reference-not-a-secret`. This fix alone
resolved most of the round-2 findings across restsharp, client-go, and
django.

### Fix 3 — documentation truncation markers not recognized (found round 2)

sinatra's own README documents setting a session secret with
`SESSION_SECRET=99ae8af...snip...ec0f262ac` — a conventional
truncated/elided example. No real credential format contains a run of
3+ literal dots. **Fix**: `_value_looks_truncated` treats any value
containing `...` as an unambiguous placeholder signal, same tier as the
existing marker-word check. Test:
`test_find_secrets_flags_a_truncated_documentation_example_as_likely_placeholder`.
Pilot case: `010-truncated-example-not-a-secret`.

### Fix 4 — GitHub's own OpenAPI spec example credentials not recognized (found round 2)

penny-bot vendors GitHub's official OpenAPI spec
(`github/rest-api-description`), which documents its example App/
installation JSON using fixed values — `client_secret:
1726be1638095a19edd134c77bde3aa2ece1e5d8`, `webhook_secret:
e340154128314309424b7c8e90325147d99fdafa`, and installation tokens
`ghs_16C7e42F292c6912E7710c838347Ae178B4a` /
`ghu_16C7e42F292c6912E7710c838347Ae178B4a` — identical across every repo
that vendors the spec verbatim, which is common. High-entropy and
non-repeating, so neither the marker-word nor repetition check caught
them. **Fix**: added to `KNOWN_VENDOR_EXAMPLE_VALUES`, same mechanism
already used for Stripe's published test key. Test:
`test_find_secrets_recognizes_githubs_own_published_openapi_example_values`.

### Fix 5 — "default" missing from placeholder value markers (found round 2)

Django's own mail-config migration docs use `"password":
"default-password"` as a documented example. **Fix**: added `"default"`
to `PLACEHOLDER_VALUE_MARKERS`.

### Fix 6 — PEM boilerplate-formatting code matched as an embedded key (found round 2, fixed in follow-up)

`okhttp`'s own `HeldCertificate.kt` — the source of its
`privateKeyPkcs8Pem()`/`privateKeyPkcs1Pem()` PEM-formatting functions —
matched `private_key_header` at a normal (non-test) path twice
(`append("-----BEGIN PRIVATE KEY-----\n")` followed by a function call,
not literal key material). Initially scoped as a deferred follow-up
(narrower than the other fixes — found in 1 of 20 repos — and needing
lookahead across lines, which none of `_is_likely_placeholder`'s other
checks need), then closed once the rest of the sweep was done. **Fix**:
a real embedded PEM block always has a base64 body (conventionally
64-76 chars/line per RFC 7468) between its BEGIN/END lines; boilerplate
string-building code doesn't. `_private_key_header_has_no_body` checks
up to 3 non-blank lines after a header match for a run of 32+ base64-
alphabet characters (comfortably above any single code identifier
encountered next to a match in this run — the longest,
`encodeBase64Lines(`, breaks at 17 chars before the `(` — and
comfortably below a real body line's length); no run found means no
body, treated as a placeholder regardless of path, same tier as the
marker-word check. Scoped to `find_secrets` only —
`find_secrets_in_history` streams individual diff-added lines and has no
equivalent "next line" to look at. Guarded against its own false-positive
risk (a real key body quoted inside a doc comment or markdown table,
prefixed with `` * `` or `|` per line, must still count as a body) with
a dedicated regression test. Tests:
`test_find_secrets_flags_a_private_key_header_with_no_body_as_likely_placeholder`,
`test_find_secrets_recognizes_a_key_body_wrapped_in_comment_markup`.
Pilot case: `011-pem-header-boilerplate-no-body`. Re-verified against the
real file: `HeldCertificate.kt`'s two boilerplate matches (lines 164,
178) now report `likely_placeholder: true`; its third match (line 529,
a KDoc comment with a real quoted example body) correctly stays
unflagged, unchanged by this fix.

### What's left unflagged, on purpose

21 of the 96 findings remain unflagged after all six fixes — reviewed
individually, not chased further, for two different reasons:

1. **Genuinely ambiguous test-path values (~15, mostly django, one
   react)** — realistic-looking fake secrets (test passwords, fake API
   keys) under `tests/`/`__tests__/` paths, with entropy above the
   low-entropy threshold. This is deliberate, existing, tested behavior
   (`test_find_secrets_does_not_downgrade_a_real_looking_secret_under_a_test_path`):
   a real leaked secret could hide at exactly this kind of path, and
   nothing about the *value itself* distinguishes it from an
   intentionally realistic test fixture. Not a bug — a real trade-off,
   reviewed and left as-is.
2. **PEM blocks with a real base64 body outside a test path, and one
   genuinely real embedded API key (~6, all in okhttp/penny-bot)** —
   `okhttp`'s changelog, `okhttp-tls/README.md`, and `HeldCertificate.kt`
   line 529 (a KDoc example) embed real-looking example certificates in
   documentation — no decisive signal separates "real leak in docs" from
   "documented example" the way the boilerplate-code case in Fix 6
   could (a doc example's body is indistinguishable in shape from a real
   key's), so these stay visible by design, same trade-off as case 1.
   `RequestBodyCompression.java`'s `GOOGLE_API_KEY` is OkHttp's own real,
   intentionally-public sample-recipe key — a correct true positive, not
   a bug: flagging a real hardcoded key is the scanner doing its job,
   regardless of the project's own risk tolerance for it.

No further gaps were found in this sweep beyond these six — every
unflagged finding remaining was individually reviewed and falls into one
of the two categories above.

### Vulnerabilities: 231 findings, spot-checked

Package names, pinned versions, and advisory ids (real GHSA/PYSEC/GO
identifiers) all check out as well-formed and plausible across every
ecosystem sampled — PyPI, npm, Go, crates.io (`Cargo.lock`), RubyGems
(gemspec fallback), Packagist (`composer.json`), NuGet (central package
management via `Directory.Packages.props`), and Gradle (version catalog
`libs.versions.toml`). Confirms `check_vulnerabilities` correctly parses
all of these real manifest shapes at meaningful scale, not just the one
hand-picked manifest per ecosystem the synthetic pilot samples. Swift
(`Package.resolved`) also parsed correctly via penny-bot but returned 0
findings (plausible - not independently re-verified). Not spot-checked
line-by-line the way the pilot's 4 hand-picked CVEs were verified
against OSV.dev directly.

**Caveats specific to this real-repo run**: repos chosen because they
were already vetted elsewhere in this codebase or picked to fill
ecosystem gaps, not randomly sampled — not a claim about OSS code in
general. Secrets ground truth here is manual eyeball review of match
location and real file content (not just the redacted preview), not a
pre-existing label. If a genuinely live, sensitive credential is ever
found this way in a third-party repo in a future run: never reproduce
the actual value anywhere (`match_preview` is already a salted hash,
safe to log), exclude it from any published report, and flag it to the
user for a responsible-disclosure decision rather than including it in
this benchmark's output. (No such value was found in either round — the
GitHub OpenAPI example values in Fix 4, the OkHttp sample key, and every
PEM block found are all publicly documented, intentionally-shared, or
already-flagged-correctly cases, not private leaks.)

## Suggested next steps (not started)

- ~~Expand vulnerabilities pilot corpus: remaining ecosystems not yet
  exercised at the pilot-case level~~ — **done**, see cases 006-017 above
  (all 10 ecosystems, 10/10 recall, 0/7 false positives, zero gaps found).
- Expand the real-repo sample further (more repos per ecosystem) for a
  larger-denominator FP-rate number - 21,430 files is much stronger than
  the original 2,325, but still a fixed, non-random sample.
- Consider a named comparison (gitleaks/trufflehog for secrets,
  osv-scanner for vulnerabilities) now that Aletheore's own numbers are
  established, mirroring `pr-review-benchmark`'s PR-Agent/DeepSource
  comparison - explicitly deferred at this benchmark's design stage in
  favor of Aletheore-only accuracy first.
