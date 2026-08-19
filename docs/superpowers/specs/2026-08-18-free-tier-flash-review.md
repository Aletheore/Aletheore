# Spec: Flash Review for the free plan, on free model providers

## Why

Flash Review is currently 100% paid-only: `run_flash_review_job` returns immediately on
`installation["plan"] == "free"` (`github-app/scan_worker/jobs.py:1388`). Free-plan installations
get the deterministic scanner and the GitHub Action's `scan`+`diff` comment, but zero AI-powered PR
review - the free tier and the paid tier ("Aletheore AIR") aren't meaningfully differentiated on
review quality anymore now that the deterministic side is solid, because the free tier currently
gets *no* AI review at all rather than a lesser one.

This spec adds a real (not token-gesture) free-tier Flash Review path, running on providers that
cost Aletheore nothing or near-nothing per call, so free installations get real AI PR review while
paid stays strictly better (Luna quality, deeper context caps already shipped, higher monthly review
count, no provider-availability risk).

Four API keys now exist in `github-app/.env` (gitignored, not committed) for this purpose:
`GROQ_API_KEY`, `GEMINI_API_KEY`, `OPENROUTER_API_KEY`, `OPENAI_FREE_TIER_API_KEY` (the last is a
separate OpenAI key on OpenAI's shared-traffic free-tier program - genuinely free up to
2,500,000 tokens/day for models in the mini/nano bucket gpt-5-nano falls into, confirmed against
OpenAI's own published free-tier terms, not a discounted-but-billed key - deliberately not the same
key or cost bucket as the existing `OPENAI_API_KEY` that `model_tiers.py` uses for paid-tier Luna).

## Verified today, before writing this spec

All four keys were live-tested against their real APIs (not assumed to work):

- **Groq** (`https://api.groq.com/openai/v1`) - OpenAI-compatible. `GET /models` returned 200.
  Real model IDs available right now: `openai/gpt-oss-120b` (131,072 context - primary pick, a
  real reasoning-capable instruct model) and `qwen/qwen3.6-27b` (131,072 context - fallback pick
  within Groq itself if desired, not required for v1).
- **Gemini** - has an OpenAI-compatible endpoint at
  `https://generativelanguage.googleapis.com/v1beta/openai` (note: `/openai` suffix, not the
  native `v1beta` surface) that accepts a standard `Authorization: Bearer` header and standard
  OpenAI-shaped chat-completion requests. Live-tested with `model: "gemini-3.5-flash"` (real,
  currently-listed model, 1,048,576 context) - got a real 200 response back through this endpoint.
  This means Gemini needs **no special-cased adapter code** - it fits the same
  `OpenAICompatibleAdapter` class every other provider in this codebase already uses.
- **OpenRouter** (`https://openrouter.ai/api/v1`) - OpenAI-compatible, already how OpenRouter is
  meant to be used. `GET /auth/key` confirmed `"is_free_tier": true`. Real `:free`-suffixed models
  available right now include `nvidia/nemotron-3.5-lightning:free` (1,000,000 context - primary
  pick) and `cohere/north-mini-code:free` (256,000 context, code-specialized - worth Nemotron
  evaluating as an alternative primary, since it's purpose-built for code rather than general chat).
- **OpenAI free-tier key** (`https://api.openai.com/v1`) - `GET /models` 403'd (restricted-scope
  key, missing `api.model.read` - harmless, this path never needs to list models). A real
  `POST /chat/completions` call with `model: "gpt-5-nano"` returned a real 200, resolving to
  `gpt-5-nano-2025-08-07`. **Reasoning-token gotcha, verified live, not assumed**: with no
  `reasoning_effort` set, all completion tokens went to hidden reasoning and the actual reply came
  back empty (`finish_reason: "length"` with 0 content). Unlike Luna (`model_tiers.py`'s
  `NO_THINKING_OPENAI = {"reasoning_effort": "none"}`), nano does **not** support `"none"` - that
  value 400s with `Supported values are: 'minimal', 'low', 'medium', and 'high'`. Using
  `"reasoning_effort": "minimal"` fixed it: 0 reasoning tokens, real content back. Use
  `extra_body={"reasoning_effort": "minimal"}` on this adapter - don't reuse
  `NO_THINKING_OPENAI` as-is, it will 400 against this specific model. This key **is genuinely
  free** up to a real daily limit (OpenAI's shared-traffic free-tier program: 2,500,000
  tokens/day for the mini/nano model bucket gpt-5-nano falls into, confirmed against OpenAI's own
  published terms - not a discounted-but-billed key as originally assumed when this spec was first
  drafted) - see the cap section below for the real daily allowance boundary this needs.

All four provider/model combinations above are real, live-verified as of today, not guesses. Model
availability (especially OpenRouter's `:free` list) does shift over time - re-verify the exact IDs
against each provider's live `/models` endpoint at implementation time rather than trusting this
spec's snapshot blindly if it's been more than a few weeks.

## What to build

### 1. A free-tier adapter chain in `model_tiers.py`

Add a new function, `writing_adapter_chain_for_free_tier(on_usage=None) -> list[OpenAICompatibleAdapter]`,
building one `OpenAICompatibleAdapter` per provider whose API key is actually configured (reuse the
existing `has_api_key(env_var, name)` helper already imported in this file - same pattern
`_openai_available()` already uses), in this priority order:

1. Groq - `base_url="https://api.groq.com/openai/v1"`, `api_key_env_var="GROQ_API_KEY"`,
   `model="openai/gpt-oss-120b"`.
2. Gemini - `base_url="https://generativelanguage.googleapis.com/v1beta/openai"`,
   `api_key_env_var="GEMINI_API_KEY"`, `model="gemini-3.5-flash"`.
3. OpenAI free-tier key - `base_url="https://api.openai.com/v1"`,
   `api_key_env_var="OPENAI_FREE_TIER_API_KEY"`, `model="gpt-5-nano"`,
   `extra_body={"reasoning_effort": "minimal"}` (see the reasoning-token gotcha above - required,
   not optional, or this adapter silently burns its whole token budget on hidden reasoning and
   returns empty findings). Gated behind the real daily token cap below - excluded from the chain
   entirely once that cap is hit for the day, not just left in to fail.
4. OpenRouter - `base_url="https://openrouter.ai/api/v1"`, `api_key_env_var="OPENROUTER_API_KEY"`,
   `model="nvidia/nemotron-3.5-lightning:free"`. Last in the chain - the weakest/most
   rate-limit-prone free option of the four (20 RPM / 50 RPD on no purchased credits, confirmed
   against OpenRouter's own docs), tried only once everything else has failed.

Skip (don't include in the returned list) any provider whose env var isn't set - same
"never hard-fail on missing infra, log and move on" principle `_openai_available()` already
follows. If the list ends up empty (no free-tier keys configured at all, e.g. local dev), the
caller should behave exactly like today: no free-tier Flash Review runs, nothing crashes.

### 2. A cascading-fallback caller, not just a single adapter pick

The paid-tier path (`writing_adapter_for`) only falls back once, at construction time, based on
whether a key exists - it never retries mid-request. Free-tier providers are meaningfully more
likely to rate-limit or error at *request* time (that's the whole reason four separate providers
exist here instead of one), so this needs real runtime fallback, not just a build-time pick.

Add a wrapper (e.g. `run_with_free_tier_fallback(adapters: list[OpenAICompatibleAdapter], fn)`
in `model_tiers.py` or directly where `review_diff()` calls out) that tries each adapter in the
chain in order, catching the adapter's own request-level exceptions (rate limit / 429, timeout,
5xx, auth failure), logging which provider failed and why, and moving to the next adapter - only
raising if every adapter in the chain fails. Log which provider actually served the successful
request (same reasoning as `resolve_model()`'s docstring: never let a spend/usage record end up
mislabeled against a provider that didn't actually run).

### 3. Wire it into `run_flash_review_job`

`github-app/scan_worker/jobs.py:1388` currently reads:

```python
if installation is None or installation["plan"] == "free":
    return
```

This needs to become a branch, not a block: `installation is None` still returns immediately (no
installation row means nothing to review against), but `plan == "free"` should route to the new
free-tier path instead of returning. The rest of `_run_flash_review` (diff fetching, evidence
context building, blast-radius, `review_diff()`) stays shared between both tiers - only the model
adapter and the caps below differ. Read the full body of `_run_flash_review` and `review_diff()`
(`github-app/scan_worker/flash_review.py:815` onward) before touching this - `review_diff()`
currently constructs its adapter internally via `writing_adapter_for(FLASH_REVIEW_FALLBACK_MODEL,
on_usage=on_usage)` at `flash_review.py:858`, which will need a plan-aware branch (or a passed-in
adapter/adapter-chain parameter) rather than always building the paid-tier adapter.

### 4. A free-tier-appropriate cap, not the existing dollar cap

`base_cap_for_plan("free")` returns `0.0` today because `PLAN_MONTHLY_PRICE_USD` has no `"free"`
entry - that $0 cap is *why* free installations are blocked (see the comment at
`jobs.py:1871-1872`). Do not just remove the plan check and let free installations fall through to
the existing dollar-cap logic - it will always read as already-exceeded and silently produce zero
reviews, which looks like this feature works but doesn't.

Free-tier needs its own cap, shaped around request count rather than dollars, since three of the
four providers are genuinely free:

- Add `MAX_FREE_TIER_FLASH_REVIEWS_PER_MONTH` (pick a real conservative number - suggest starting
  at half of whatever `MAX_FLASH_REVIEWS_PER_MONTH` currently is, so paid stays clearly ahead; find
  that constant's current value and current location before picking the exact number). Reuse the
  existing `get_flash_review_count_this_month` / `check_and_reserve_flash_review_attempt` machinery
  already in this file rather than building new counting infrastructure.
- The OpenAI free-tier key is on OpenAI's shared-traffic free-tier program, not a discounted-but-
  billed key - genuinely free up to 2,500,000 tokens/**day** (confirmed against OpenAI's own
  published free-tier terms; gpt-5-nano falls in the mini/nano bucket that gets this allowance,
  separately from the 250k/day bucket for full-size models). This is a real daily allowance
  boundary, not a spend ceiling - give it its own cap at 2,400,000 tokens/day (100k short of the
  real 2.5M limit as a safety margin, since usage is checked once per Flash Review rather than per
  token). Track it with a Redis counter scoped to the calendar day (UTC), incremented by
  prompt+completion tokens on usage, checked *before* this provider is added to the chain at all
  (not just before it's attempted) - don't add a new Postgres table for this. If the cap is hit for
  the day, exclude the OpenAI step from the chain entirely (log it) rather than failing the whole
  review - Groq and Gemini having already failed by the time the chain would reach OpenAI is the
  only case this matters for anyway.

## Out of scope for this spec

- No changes to the paid tier's model routing, caps, or `writing_adapter_for_plan` /
  `model_for_plan` - those are untouched.
- No UI/pricing-page changes - this is a backend capability change only. Whether/how to advertise
  "free tier now includes AI review" on the marketing site is a separate decision, not part of this
  spec.
- No changes to managed audits, AIRview, or Docs generation - this spec is Flash Review only.
- Don't build a live `/models` re-check into the request path (e.g. querying OpenRouter's free-model
  list at runtime) - static model IDs from this spec are enough for v1. Re-verifying they're still
  live before implementing is fine; building dynamic model-discovery is not in scope.

## Verification (mandatory - do this for real, not as a claim)

1. Run the full `github-app` test suite. Report real pass/fail counts.
2. Add real tests for the new cascading fallback: at minimum, one test where the first N adapters
   in the chain raise and the last one succeeds (verify the result comes from the last one, and
   that each earlier failure was logged), and one test where every adapter fails (verify the
   caller's existing failure-comment path still fires, same as today's `except Exception` in
   `run_flash_review_job`).
3. Real end-to-end check against at least one real case from `benchmarks/pr-review-benchmark/cases/`:
   run the free-tier chain for real (real API calls, not mocked) against one small case and one
   larger case, with a real GitHub App test installation set to `plan = "free"`. Confirm an actual
   PR comment gets posted, confirm the monthly free-tier review counter increments, confirm the
   dollar-cap Redis counter increments only on the OpenAI-key path (force the first three providers
   to fail for one of the two runs, e.g. by temporarily using a bad model name, to confirm the
   OpenAI fallback and its cap actually engage - don't just test the happy path where Groq succeeds
   first and the rest of the chain never gets exercised).
4. Report which provider actually served each test call, and the real latency/cost for each -
   this is genuinely useful operational data for setting `MAX_FREE_TIER_FLASH_REVIEWS_PER_MONTH`
   sensibly, not just a formality.
