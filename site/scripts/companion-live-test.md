# Running the grounded companion evaluation

The regular website test suite skips network tests. It never reads a root `.env` or spends model credits.

To run the live evaluation, supply `INDIEROUTER_API_KEY` securely in the shell environment, then run from the repository root:

```sh
RUN_COMPANION_LIVE=1 COMPANION_EVAL_REPEATS=2 npm --prefix site test -- --run tests/companion-live.test.ts
```

The harness sends only synthetic questions in `site/tests/companion-eval-cases.ts` plus allowlisted public documentation. It has a 400,000-token check between requests, at most 42 cases per round, and at most 3 rounds. `COMPANION_EVAL_LIMIT` can lower the count. It writes results to `/private/tmp/aletheore-companion-live-eval.json` by default; `COMPANION_EVAL_OUTPUT` can set an OS-appropriate output path. Prices in the report are estimates at published list rates without cache discounts, not the provider's invoice.

For browser review, launch Next dev with `COMPANION_LIVE_ENABLED=1` and the key in its server environment. Bind to `127.0.0.1`. Never use a `NEXT_PUBLIC_` variable for the key or copy the root `.env` into the website. Without that flag and key, development retains the labelled fixture preview.

The endpoint deliberately rejects production requests even if these environment variables exist. The endpoint now uses the authenticated backend budget gate and persistent Redis reservations; it has no process-local fallback. Local review also requires the limiter service URL/token and initialized ledger. Public launch still requires a Vercel preview check, provider-side spending cap, privacy-policy updates and deployment approval. Do not remove the production gate as a substitute for those controls.

The live model first classifies scope, then selects source IDs. The server returns the exact approved text and commands for validated IDs, never arbitrary model prose or links. This limits fabrication but does not prove semantic relevance or source correctness. Add regression cases for every observed failure and review sources when product documentation changes.

## Extended injection suite

Run 50 additional adversarial/benign-control cases twice:

```sh
RUN_COMPANION_LIVE=1 COMPANION_EVAL_SUITE=injection COMPANION_EVAL_REPEATS=2 COMPANION_EVAL_OUTPUT=/private/tmp/aletheore-companion-injection-eval.json npm --prefix site test -- --run tests/companion-live.test.ts
```

The case count is 50 for the injection suite and 42 for the original product suite. Both retain the same total token check and three-round maximum. HTTP boundary checks must use the preview's canonical `http://localhost:3101` origin; an IP-address alias can be rejected earlier by the same-origin gate.
