# Shield review follow-up

Written November 2, 2026 in Asia/Kolkata (UTC+05:30); this is still November 1 in UTC.

The review reproduced three defects: git-ignored registry settings could expose private dependency names to public registries; config/text changes could reuse stale secret evidence; and `shield off` deleted a pre-existing config and its offline preference. The fixes read applicable ignored registry settings, invalidate evidence for secret-scannable inputs and deleted known files, and remove the config only when setup created it.

The branch also merges current master to resolve the changelog conflict, preserving both sets of entries. Checkpoint fixtures explicitly write LF bytes on Windows. Repository snapshot fixtures disable automatic Git maintenance, so transient maintenance locks cannot invalidate file-preservation assertions. CodeQL's test URL checks now compare extracted hosts; terminal control escaping uses numeric code points instead of an ambiguous regex range.

## Checked Flash findings

Numbers below refer to the original 36-finding review. A finding being syntactically grounded in a diff does not establish that its behavioral claim is correct.

| Finding | Disposition |
| --- | --- |
| 1, suspicious package data | Verified against the cited upstream datasets; retained the names. See provenance below. |
| 2, silent receipt failure | Internal failure produces a receipt with an explicit not-checked reason. `None` remains reserved for idle sessions. |
| 3, dotenv suffix | Added case-insensitive `*.env` exclusion from checkpoints. |
| 4, dedupe before delivery | Commit dedupe state only after output writes and flushes succeed. |
| 5, hook-state lost updates | Serialize the read/merge/replace with a bounded OS file lock. |
| 6, setup.cfg metadata | Parse only dependency sections using ConfigParser. |
| 7, declared-name substring | Require a separator boundary and the correct direction: declaring `foo` does not cover a new `foo_extra` import. |
| 8 and 28, JS comment/string extraction | Read import/export/require syntax from the existing TypeScript/TSX parser. Comments, strings and regex contents cannot invent imports or delete later imports. |
| 9, partial base manifest set | Report the limit and skip manifest-derived comparisons instead of comparing a complete head with a truncated base. |
| 10, overlapping replacement cycle | Report replacement groups such as old A-B becoming A-C; preserve existing pure growth/shrink behavior. |
| 11, late evidence work | Do not begin evidence work after the guard deadline; share remaining time across changed-file Git commands. Existing hook wrappers still impose their independent 2/8-second hard stops. The guard's budget is soft; this does not claim every internal scan operation is interruptible. |
| 12, receipt retention skew | Use write-time wall clock for retention, independently of receipt timestamps. |
| 13, refresh marker race | Serialize freshness inspection and marker replacement with a bounded OS lock. |
| 14, ambiguous import mapping | Report not checked when neither installed nor declared distribution evidence disambiguates the import. |
| 15, HTTP-error close exception | Intentional best effort: failure to close the response must not replace its actual lookup result. |
| 16, receipt-prune exception | Intentional best effort: failure to tidy old receipts must not turn a successful receipt write into failure. |
| 17, debug-output exception | Intentional best effort: a broken diagnostic stream must not break a hook. |
| 18, output-write exception | Writes return delivery status; failed delivery no longer suppresses future findings (see 4). |
| 19, import mappings | Add the legacy `Cryptodome` → `pycryptodomex` mapping and additional pywin32 namespaces. The suggested `Cryptodome` → `pycryptodome` mapping was incorrect. |
| 20, config durability | Atomic temporary-file replacement preserves the previous offline preference when a save fails. |
| 21 and 29, Git errors | Distinguish timeout, spawn failure and actual Git exits using internal sentinels outside normal exit/signal ranges. |
| 22, cache bounds | Bound reads and writes to 1 MiB and 2,048 entries; preserve a newly checked entry on timestamp ties, and remove failed-write temporary files. |
| 23, lookup failure wording | Distinguish a failed registry lookup from a registry that was not checked. |
| 24, pytest command paths | Quote suggested test paths using the platform's argv formatter. |
| 25, deleted-file freshness | Deleted known source/secret paths invalidate evidence instead of disappearing from freshness checks. |
| 26, orphaned state | Hourly housekeeping prunes inactive hook-state files older than seven days. |
| 27, ecosystem fallback | Unsupported ecosystems cannot silently select the npm near-miss index. |
| 30 and 31, Windows command quoting | Already marked resolved at the reviewed head. The executable is quoted; remaining Shield arguments are fixed command tokens. No arbitrary-argument portability claim added. |
| 32–34, specification drift | Correct event budgets, pre-existing config and retained evidence exclusion, and the shared user-home registry cache. |
| 35, EOF newline | Added the data file's trailing newline. |
| 36, registry lost updates | Lock the shared cache read/merge/replace; bounded lock failure simply skips caching. |

## Package-data provenance check

The alleged planted names all occur in the current top 1,000 of the dataset the file cites: [`hugovk/top-pypi-packages`](https://hugovk.github.io/top-pypi-packages/top-pypi-packages.min.json), last updated November 1, 2026 at 06:41:05 UTC. Their ranks are `uncalled-for` 394, `httpx2` 98, `httpcore2` 100, `nest-asyncio2` 556, `fastar` 565, `python-discovery` 167, `librt` 201, `ast-serialize` 291, `mcp-types` 280 and `langchain-protocol` 403. The bundled snapshot shares 964 names with the current top 1,000. This verifies the disputed provenance; it is not a security endorsement of each package.

The npm list exactly matches the first 1,000 names in [`wooorm/npm-high-impact/lib/top.js`](https://raw.githubusercontent.com/wooorm/npm-high-impact/main/lib/top.js). Download SHA-256 values were `3e958a64ebe1de0b6a4ab4c6b465d481f350fd1dfa926c639add36eb34dabc1e` (PyPI) and `bbc16e783283f23d38559a1322d93af27b8a7a884b1fa9c5a1dd84e810684f0c` (npm). The package lists were not regenerated during this fix.

The namespace mapping was checked against the [PyCryptodome installation documentation](https://www.pycryptodome.org/src/installation): `Crypto` belongs to `pycryptodome`, whereas `Cryptodome` belongs to the legacy `pycryptodomex` distribution.

## Validation

Regression coverage exercises private ignored and nested registry settings with an intercepted opener, secret-input freshness and deletion, pre-existing config preservation, failed config replacement, real JS syntax, dependency ambiguity and manifest limits, concurrent state/cache/marker updates, receipt failures and retention, failed output delivery, shared deadlines and quoted test paths. No real private package name is sent to a registry by these tests.

The Shield modules plus evidence-hook, test-selection, package-data and UI suites passed (701 tests). A subsequent run of registry/review regressions/setup and graph/signature/cycle suites passed (222 tests), including the final cache changes; these runs overlap and should not be added together. Ruff and whitespace checks passed. Cross-platform CI remains the authority for Windows and macOS fixtures. A broader local watcher run crashed in background SQLite graph loading; that run is not reported as passing.

## Second Flash review

The second review contains 23 comments. The following dispositions refer to its numbering, independently of the first review's numbering above.

| Finding | Disposition |
| --- | --- |
| 1, schemeless npm registry | Unsupported premise: npm requires a registry URL. A local `npm config set registry registry.npmjs.org` probe explicitly warns that it must be a full URL and omits the value. Keep unknown/invalid registry settings fail closed. [npm config reference](https://docs.npmjs.com/using-npm/config/#registry). |
| 2, bounded hook-state lock | A failed save returns False with debug diagnostics; failure to record the pre-edit marker also emits an explicit warning that turn-end checks may be skipped. Bounded waits preserve hook hard stops. |
| 3, overly short cleanup window | The cited constant already equals seven days. Give hook-state retention its own seven-day constant and serialize prune with writers so a refreshed session cannot be removed by a stale check. |
| 4, declared suffix match | Remove generic suffix matching; declaring `foo-bar` no longer hides a new `bar` import. |
| 5, malformed secret evidence | Include extraction inside the shape guard; request a scan instead of leaking AttributeError. |
| 6 and 17, receipt error-handler probes | Retain already-computed session status and timestamp, so failure reporting makes no second state probe. |
| 7 and 8, lock error handling | Retry only contention errno values, and let handle close release a lock if explicit unlock fails; do not mask the protected operation's original error. |
| 9, Git resolution error | Include executable resolution inside the spawn-failure conversion, not only subprocess invocation. |
| 10, self-cycles | Old individual self-imports do not make a new mutual cycle familiar. |
| 11, deleted-file refresh loop | Successful scans rebuild module/secret inventories from existing files. Failed refreshes must continue reporting stale evidence; marker freshness and refresh intervals already throttle attempts. Do not mark stale evidence clean to suppress a retry. |
| 12 and 14, near-zero listing budget and wording | Skip Git spawning with less than 10 ms remaining; handle listing timeout as budget exhaustion. |
| 13, parser dependencies | Both packages are existing required dependencies in `src/pyproject.toml`, with compatible version ranges; no new installation requirement. |
| 15, escaped JS specifiers | Decode Unicode/hex/static string escapes before package validation; malformed/control escapes are skipped. The earlier code rejected backslashes rather than sending a wrong name. |
| 16, Windows sentinel range | Use sentinels outside the entire 32-bit process-exit range. |
| 18, parser allocation | Reuse one parser per thread, avoiding shared mutable parser state. |
| 19 and 20, specification precision | Explicitly document the CLI's soft budget and case-insensitive checkpoint basename matching. |
| 21, sibling registry discovery | Unsupported: discovery uses all working files, not only changed files. Applicable ancestor configs of any visible package file are included. A config in an unrelated sibling package does not configure the changed package. |
| 22, future-dated filename | Unsupported: the note uses the actual local writing date; the timezone is now explicit. |
| 23, dictionary ordering | Incorrect: `pytest` sorts before `pythoncom` (`e` before `h` after `pyt`). Existing order is correct. |

The deterministic scanner's generic subprocess, fallback-label and best-effort cleanup flags were inspected individually. Narrow Bandit suppressions explain why each is safe; malformed dependency declarations now catch specific parse/type errors. Bandit reports zero findings in Shield. The complete affected Shield/adjacent suite was rerun after the second-round changes: 787 passed, with Ruff and whitespace checks also passing.

The third review's nine comments were also checked. Failure receipts now retain an explicit caller timestamp even if the initial state probe fails; Git spawn failures are reported as an environment skip; partial-timeout wording and prune-lock diagnostics are clearer. The affected guard/hook/receipt/review suites passed (231 tests). The other claims overlook existing safeguards: `_read` already absorbs OSError per file (verified by injecting PermissionError while another declaration remains readable); the outer fdopen context closes the lock handle unconditionally; every state update replaces the file and refreshes its mtime, and pruning excludes the current session; executable resolution either returns an absolute path or raises; and actual backslash-plus-line-terminator continuation differs from a literal Unicode escape (verified for U+2028). The latest deterministic scan reports no new static-analysis findings; its neutral status discloses that hosted Semgrep is disabled.
