# PR 1158 Flash review audit

Reviewed all 32 inline threads against the current branch. Bot suggestions were checked against
source and regressions before changes.

| Thread                                                                        | File                                                              | Disposition                                                                                                                                                                                  |
| ----------------------------------------------------------------------------- | ----------------------------------------------------------------- | -------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| [1](https://github.com/Aletheore/Aletheore/pull/1158#discussion_r4399289937)  | `src/aletheore/mcp_server.py`                                     | Fixed together with thread 12: meter bookkeeping failures now emit debug diagnostics without contaminating MCP output.                                                                       |
| [2](https://github.com/Aletheore/Aletheore/pull/1158#discussion_r4399290152)  | `src/aletheore/skills_browser.py`                                 | No change needed: str.partition returns the entire remainder, including every subsequent newline. Added multiline-header regression coverage.                                                |
| [3](https://github.com/Aletheore/Aletheore/pull/1158#discussion_r4399290340)  | `src/aletheore/skills_browser.py`                                 | Fixed: plain string headers now receive the same shared heading as Text headers; arbitrary renderables remain caller-owned.                                                                  |
| [4](https://github.com/Aletheore/Aletheore/pull/1158#discussion_r4399290504)  | `src/aletheore/cli_presentation.py`                               | No duplicate reproduced: all leaf help and five nested-group help routes assert exactly one heading.                                                                                         |
| [5](https://github.com/Aletheore/Aletheore/pull/1158#discussion_r4399290704)  | `src/aletheore/cli_presentation.py`                               | Not a valid invocation: tokens has no --path option. That option belongs to tokens meter. Added a usage-error regression for the reported ordering.                                          |
| [6](https://github.com/Aletheore/Aletheore/pull/1158#discussion_r4399290892)  | `src/aletheore/cli_presentation.py`                               | Retained intentionally: interactive errors may carry the shared branding on stderr; stdout and exit codes remain intact. This does not violate the presentation contract.                    |
| [7](https://github.com/Aletheore/Aletheore/pull/1158#discussion_r4399291127)  | `docs/superpowers/specs/2026-10-28-cli-presentation-standard.md`  | Verified resolved: shared UI uses reverse video for menu selection; headings use bold/dim.                                                                                                   |
| [8](https://github.com/Aletheore/Aletheore/pull/1158#discussion_r4399291371)  | `site/src/app/globals.css`                                        | Fixed: reduced motion disables animations without overriding the requested pose's background position.                                                                                       |
| [9](https://github.com/Aletheore/Aletheore/pull/1158#discussion_r4399291598)  | `site/tests/mascot.test.ts`                                       | Fixed: assert each pose's exact expected grid coordinate, not only rounded uniqueness.                                                                                                       |
| [10](https://github.com/Aletheore/Aletheore/pull/1158#discussion_r4399291796) | `site/tests/mascot.test.ts`                                       | Fixed: render Logo, Hero and every mascot pose with React server rendering; CSS assertions target reduced-motion semantics.                                                                  |
| [11](https://github.com/Aletheore/Aletheore/pull/1158#discussion_r4399292001) | `src/aletheore/read_guard.py`                                     | Documented: setup-id identifies exact installer-owned entries for removal. It is not authentication; explicitly invoking the module is itself opt-in.                                        |
| [12](https://github.com/Aletheore/Aletheore/pull/1158#discussion_r4399292211) | `src/aletheore/read_guard.py`                                     | Documented intentionally pure API: pre_read_decision evaluates; main_pre records actual emitted denials. Preview/evaluation calls must not inflate savings.                                  |
| [13](https://github.com/Aletheore/Aletheore/pull/1158#discussion_r4399292396) | `src/aletheore/mcp_server.py`                                     | Fixed: best-effort meter errors are debug-logged and never fail MCP responses.                                                                                                               |
| [14](https://github.com/Aletheore/Aletheore/pull/1158#discussion_r4399292574) | `src/aletheore/mcp_server.py`                                     | Fixed: \_metered_budget reuses original/final measurements and final encoded response. Regression checks two serializations, no extra meter serialization.                                   |
| [15](https://github.com/Aletheore/Aletheore/pull/1158#discussion_r4399292726) | `src/aletheore/token_meter.py`                                    | Fixed warning wording: unreadable/unsafe state paths and corrupt files are now described accurately without changing the summary schema.                                                     |
| [16](https://github.com/Aletheore/Aletheore/pull/1158#discussion_r4399292902) | `src/aletheore/token_meter.py`                                    | Documented existing contract: naive timestamps mean local wall time, matching summarize(now=...). Existing regression covers local-day bucketing.                                            |
| [17](https://github.com/Aletheore/Aletheore/pull/1158#discussion_r4399293101) | `docs/superpowers/specs/2026-10-28-token-savings-meter-design.md` | Fixed spec: explicit --yes is documented as unattended consent alongside typed yes.                                                                                                          |
| [18](https://github.com/Aletheore/Aletheore/pull/1158#discussion_r4399293287) | `src/aletheore/read_guard_setup.py`                               | Fixed: recheck ownership before settings mutation; if it changes afterward, report that hooks were removed and preserve the changed ownership record. Both races have regressions.           |
| [19](https://github.com/Aletheore/Aletheore/pull/1158#discussion_r4399293527) | `src/aletheore/read_guard_setup.py`                               | Fixed remaining gap: rollback also handles OSError/UnicodeError, with a settings-write PermissionError regression.                                                                           |
| [20](https://github.com/Aletheore/Aletheore/pull/1158#discussion_r4399293698) | `src/aletheore/cli.py`                                            | Already fixed in current code: TyperOption is used, and click is imported. Full nested-menu regression coverage passes.                                                                      |
| [21](https://github.com/Aletheore/Aletheore/pull/1158#discussion_r4399293877) | `src/aletheore/cli.py`                                            | Fixed: an empty required answer prints a cancellation notice and does not execute the command.                                                                                               |
| [22](https://github.com/Aletheore/Aletheore/pull/1158#discussion_r4399294084) | `src/aletheore/cli.py`                                            | No change needed: skill-hub is registered with app.add_typer(... hidden=True), not merely documented. Menu enumeration and nested help cover the alias.                                      |
| [23](https://github.com/Aletheore/Aletheore/pull/1158#discussion_r4400266514) | `src/aletheore/read_guard_setup.py`                               | False positive: encoding="utf-8-sig" is valid Python. Module imports and the complete test suite compile this statement.                                                                     |
| [24](https://github.com/Aletheore/Aletheore/pull/1158#discussion_r4400266710) | `src/aletheore/user_paths.py`                                     | Fixed at consent boundary: filesystem inspection errors become ReadGuardSetupError. The low-level helper deliberately propagates non-missing errors to fail closed.                          |
| [25](https://github.com/Aletheore/Aletheore/pull/1158#discussion_r4400266856) | `src/aletheore/_launcher.py`                                      | Fixed with debug logging; no unconditional stderr warning because hook/protocol output must remain silent.                                                                                   |
| [26](https://github.com/Aletheore/Aletheore/pull/1158#discussion_r4400267035) | `src/aletheore/subprocess_utils.py`                               | Fixed: invalid UTF-8 shim content is converted into actionable OSError, consistent with adapter launch-error handling.                                                                       |
| [27](https://github.com/Aletheore/Aletheore/pull/1158#discussion_r4400267200) | `src/aletheore/subprocess_utils.py`                               | Standard quoted npm shims are covered by actual Node execution with Unicode, spaces and metacharacters. Unknown/custom batch wrappers intentionally fail safely instead of invoking a shell. |
| [28](https://github.com/Aletheore/Aletheore/pull/1158#discussion_r4400267338) | `src/aletheore/tokens_cli.py`                                     | Verified already fixed: conflicting paths raise outside the normalization try block; existing regressions cover this.                                                                        |
| [29](https://github.com/Aletheore/Aletheore/pull/1158#discussion_r4400267554) | `.github/scripts/cli_smoke.py`                                    | False positive: spawn sets CREATE_NEW_PROCESS_GROUP on Windows before sending CTRL_BREAK_EVENT. Native Windows smoke matrix already passed.                                                  |
| [30](https://github.com/Aletheore/Aletheore/pull/1158#discussion_r4400267764) | `.github/scripts/cli_smoke.py`                                    | Fixed: the reader thread queues exceptions and the caller raises a chained RuntimeError immediately; controlled regression verifies it.                                                      |
| [31](https://github.com/Aletheore/Aletheore/pull/1158#discussion_r4400535853) | `src/aletheore/ui.py`                                             | Fixed: choose a placeholder marker absent from the template, with a literal U+FFFC regression.                                                                                               |
| [32](https://github.com/Aletheore/Aletheore/pull/1158#discussion_r4400536079) | `src/aletheore/ui.py`                                             | Fixed: only bare {} fields substitute values; other braces remain literal. Added JSON/unbalanced-brace regressions and documented the contract.                                              |

## Container failure

The scan-worker image gate reported CVE-2026-102554 in PMD's Guava 33.7.1 and CVE-2026-116587 in
Trivy's go-jose 4.1.5. PMD 7.28.0 and Trivy 0.76.1 remain the latest upstream releases. Replace
Guava with checksum-verified 33.7.2 and rebuild the pinned Trivy release source with go-jose 4.1.6.
The HIGH/CRITICAL security gate is unchanged.

## Follow-up review on b3e173aa

- [Thread 33](https://github.com/Aletheore/Aletheore/pull/1158#discussion_r4401134018): Strict
  bare-field/value matching is intentional. Added an AST audit of every production CLI markup call
  proving that no caller relies on unused values.
- [Thread 34](https://github.com/Aletheore/Aletheore/pull/1158#discussion_r4401134305): No partial
  destination is possible: \_write_json writes a unique temporary file, fsyncs, and atomically
  replaces the destination. Added a mid-write disk-full regression proving no sidecar/temp survives.
  Changed ownership content must be preserved.
- [Thread 35](https://github.com/Aletheore/Aletheore/pull/1158#discussion_r4401134589): Verified all
  three callers: plan_install/remove propagate the documented setup error; tokens meter catches it.
  is_installed propagates filesystem inspection failure to the same CLI handler instead of falsely
  reporting an active hook.
- [Thread 36](https://github.com/Aletheore/Aletheore/pull/1158#discussion_r4401134904): The exact
  pinned Trivy build completed successfully in CI with -mod=readonly (job122844660429); no tidy or
  unrelated dependency updates are needed.
- [Thread 37](https://github.com/Aletheore/Aletheore/pull/1158#discussion_r4401135250): Added pmd
  --version and trivy --version checks to the image build; also validating actual PMD rule execution
  in the resulting image.
- [Thread 38](https://github.com/Aletheore/Aletheore/pull/1158#discussion_r4401135552): Added a
  fallback original measurement if a future apply_budget implementation measures a copy, and a
  regression exercising that scenario.
- [Thread 39](https://github.com/Aletheore/Aletheore/pull/1158#discussion_r4401135849): The final
  identity check already handles the described ordering. Added a regression that measures bounded,
  then an unrelated candidate, then returns bounded; the response remains the bounded result.

The first rebuild also caught a checksum transcription error. Recomputed Guava's SHA-256 from the
downloaded JAR, matched Maven Central's published digest, and added a regression requiring every
download checksum in this Dockerfile to contain exactly 64 hexadecimal characters.

Windows CI caught newline translation in the new rollback fingerprint check. The atomic JSON writer
now explicitly writes LF on every OS, matching the serialized ownership fingerprint. Added an
exact-byte serialization regression; rollback still preserves a concurrently changed record.
