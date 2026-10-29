# Running the grounded companion evaluation

The regular website test suite skips network tests. It never reads a root `.env` or spends model credits.

To run the live evaluation, supply `INDIEROUTER_API_KEY` securely in the shell environment, then run from the repository root:

```sh
RUN_COMPANION_LIVE=1 COMPANION_EVAL_REPEATS=2 npm --prefix site test -- --run tests/companion-live.test.ts
```

The harness sends only synthetic questions in `site/tests/companion-eval-cases.ts` plus allowlisted public documentation. It has a 400,000-token stopping threshold checked before and after each question (not a prepaid hard cap; the final question can cross it), at most 42 cases per round, and at most 3 rounds. `COMPANION_EVAL_LIMIT` can lower the count. It writes results to `aletheore-companion-live-eval.json` inside the platform temporary directory by default; `COMPANION_EVAL_OUTPUT` can select another path. The harness checks output writability before spending, checkpoints every case, and preserves partial results when the token check or unknown usage stops the run. Prices in the report are estimates at published list rates without cache discounts, not the provider's invoice.

For browser review, launch Next dev with `COMPANION_LIVE_ENABLED=1` and the key in its server environment. Bind to `127.0.0.1`. Never use a `NEXT_PUBLIC_` variable for the key or copy the root `.env` into the website. Without that flag and key, development retains the labelled fixture preview.

The endpoint deliberately rejects production requests even if these environment variables exist. The endpoint now uses the authenticated backend budget gate and persistent Redis reservations; it has no process-local fallback. Local review also requires the limiter service URL/token and initialized ledger. Public launch still requires a Vercel preview check, provider-side spending cap, privacy-policy updates and deployment approval. Do not remove the production gate as a substitute for those controls.

The live model first classifies scope, then selects source IDs. The server returns the exact approved text and commands for validated IDs, never arbitrary model prose or links. This limits fabrication but does not prove semantic relevance or source correctness. Add regression cases for every observed failure and review sources when product documentation changes.

## Extended injection suite

Run 50 additional adversarial/benign-control cases twice:

```sh
RUN_COMPANION_LIVE=1 COMPANION_EVAL_SUITE=injection COMPANION_EVAL_REPEATS=2 npm --prefix site test -- --run tests/companion-live.test.ts
```

The case count is 50 for the injection suite and 42 for the original product suite. Both retain the same total token check and three-round maximum. HTTP boundary checks must use the preview's canonical `http://localhost:3101` origin; an IP-address alias can be rejected earlier by the same-origin gate.


## Windows and browser checks

Use Node 22.12+ (22.x), Node 24, or another version accepted by `site/package.json`; the pinned Vitest 5 toolchain does not support Node 20. Runtime/tooling engine requirements are aligned in package.json and its lockfile. The GitHub Actions matrix runs tests, lint and type checking on Windows, macOS and Linux with Node 22 and 24; the real Redis job uses the deployed Redis 7 major version on Linux.

On PowerShell, set environment variables with `$env:RUN_COMPANION_LIVE="1"` and `$env:COMPANION_EVAL_REPEATS="2"`, then run `npm --prefix site test -- --run tests/companion-live.test.ts`. Supply credentials through the environment; never paste their values into a tracked script. Unset `RUN_COMPANION_LIVE` afterward.

The no-cost UI regression is `npm --prefix site run e2e:companion`. It finds Chrome in standard Windows/macOS/Linux locations or accepts `CHROME_BIN`. It starts a local dev server with a synthetic key and intercepts every chat API request. Set `COMPANION_TEST_BASE` to an existing local preview instead if desired. Checks cover desktop/mobile dialogs, source navigation with retained history, Escape/focus, reduced motion, Stop and Clear. Browser checks run on each OS's Node 22 job. They do not establish real iOS keyboard or assistive-technology coverage. The production UI and API gates remain deliberately closed; a later public rollout needs a runtime switch instead of build-time static-layout flags.
