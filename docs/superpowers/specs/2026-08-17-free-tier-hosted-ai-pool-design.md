# Free-tier hosted AI: pooled, consented, isolated from paid traffic

**Status:** Ready for implementation — Phase 1 only (see scope below).
**Owner:** implementing agent (fast, less capable — follow this spec literally; every reuse
pointer names an exact existing function/class, not a pattern to reinvent).
**Reviewer:** Claude (review pass after implementation, before merge).

## Why this exists

Aletheore wants a genuinely free (zero marginal cost) hosted-AI tier for free/individual users,
using OpenAI's and Google's own free daily quota programs — in exchange for explicit,
disclosed consent that the code sent through it may be used by those providers to train/tune
their models. This is completely separate from and must never touch the paid tier (`gpt-5.6-luna`
via `model_tiers.py`, billed, no data-sharing).

**Models decided** (do not second-guess this choice — it was made from real benchmark data):
- OpenAI side: `gpt-5.4-nano`, called **directly** against `https://api.openai.com/v1` using an
  OpenAI account/project with "Share inputs and outputs" enabled in that account's Data Controls.
  **Not via OpenRouter, not via Azure** — those are separate commercial products with their own
  billing and, in Azure's case, the opposite data-handling policy. Only a direct `api.openai.com`
  call under an account with sharing enabled is actually free.
- Google side: `gemini-3.5-flash-lite` (confirm this exact model string against
  `https://ai.google.dev/gemini-api/docs/models` before hardcoding it — model slugs on Google's
  side are not always predictable from marketing names), called **directly** against Google AI
  Studio's API using an AI Studio key. AI Studio's free tier already shares this data by default
  under Google's own terms — no separate opt-in needed on Google's side, but Aletheore's own
  user-facing consent (see below) covers it too, for one consistent story to users regardless of
  provider.
- **Pool size**: 2 OpenAI keys, 4 Google AI Studio keys — all real credentials the user will
  provide separately, not part of this implementation task. Do not hardcode placeholder keys
  anywhere; read every key from environment variables (naming convention below).

## What already exists — reuse these, do not reimplement them

Read `github-app/scan_worker/model_tiers.py` in full first — it's short and is the existing,
working pattern for exactly this kind of thing (model selection + adapter construction) for the
*paid* tier. Also read `src/aletheore/adapters/openai_compatible.py` in full.

1. **`OpenAICompatibleAdapter`** (`src/aletheore/adapters/openai_compatible.py`) is already
   provider-agnostic — it's how DeepSeek is served today (`model_tiers.py`'s fallback path uses it
   with `base_url="https://api.deepseek.com"`), not literally OpenAI-only. **Try reusing this
   class unmodified for the Gemini side too**, pointed at Google's OpenAI-compatible endpoint
   (`https://generativelanguage.googleapis.com/v1beta/openai/` — verify this exact path against
   Google's current docs, it has moved before) with `model="gemini-3.5-flash-lite"`. Write one
   small, real, live test call (not mocked) against a real Gemini AI Studio key to confirm this
   actually works before committing to this reuse — if Google's OpenAI-compat layer has some real
   incompatibility (a specific parameter, a response shape difference), report that honestly and
   only then consider a dedicated adapter. Don't build a new adapter class preemptively.
2. **The adapter interface every caller depends on**: `simple_completion(system_prompt: str,
   user_prompt: str, cwd: str = ".") -> str`. Whatever you build must expose exactly this, so it's
   a drop-in wherever `writing_adapter_for`/`writing_adapter_for_plan` is currently called (see
   `github-app/scan_worker/jobs.py`, six call sites, `grep -n writing_adapter_for_plan`).
3. **`model_for_plan(plan: str)` and `writing_adapter_for_plan(plan, ...)`**
   (`model_tiers.py`, lines ~120-136) are the exact, already-wired integration points. Right now
   both **ignore** the `plan` argument entirely and always resolve to the paid path. This is not a
   bug you're fixing — it's the intentional hook this feature plugs into. Do not touch any other
   call site in `jobs.py`; changing these two functions alone is enough to reach every existing
   caller (managed audits, AIRview builds, fix suggestions — six call sites, all already
   plan-aware in the sense that they already pass `plan` through, just unused downstream).
4. **The consent-prompt pattern already in this codebase**:
   `_default_confirm_openai_fallback()` in `src/aletheore/search_index.py` (read it) is the
   existing, shipped shape for "explain what's about to happen, ask, don't proceed without a yes."
   Match its tone and directness for any new prompt text, don't invent a different voice.
5. **The per-installation settings-column pattern**: `github-app/migrations/037_llm_suggestions_setting.sql`
   plus `set_llm_suggestions_enabled` in `github-app/app_server/db.py` (read both) is the exact
   precedent for "add one nullable/defaulted boolean column to `installations`, add one `set_`
   helper." Follow this pattern exactly for consent storage (see below) — **except default to
   `NULL`, not `true`**: unlike that precedent (an opt-out for an existing feature), this is a new
   opt-in and must not be silently enabled for anyone.
6. **Redis-backed counting pattern**: `github-app/app_server/rate_limit.py`'s `is_rate_limited`
   (fixed-window `INCR` + `EXPIRE NX`) is the existing pattern for "count something per key, reset
   on a time window, do it atomically." Model per-key daily *token* tracking on this shape (see
   Quota tracking below) rather than inventing a different Redis pattern.

## What to build (Phase 1 only — see explicit scope boundary below)

### 1. Consent storage

New migration `github-app/migrations/053_free_tier_ai_consent.sql`:

```sql
-- Free-tier hosted AI (github-app/scan_worker/model_tiers.py's free-tier pool) requires
-- explicit, disclosed consent that code sent through it may be used by OpenAI/Google to
-- train or tune their models - this is not retroactive and not silently on. NULL means
-- "never asked" / "no decision yet", distinct from explicit false ("asked and declined").
ALTER TABLE installations
    ADD COLUMN IF NOT EXISTS free_tier_ai_consent BOOLEAN;
```

Add to `github-app/app_server/db.py`, next to `set_llm_suggestions_enabled`:

```python
async def set_free_tier_ai_consent(pool: asyncpg.Pool, installation_id: int, consented: bool) -> None:
    await pool.execute(
        "UPDATE installations SET free_tier_ai_consent = $2, updated_at = now() "
        "WHERE installation_id = $1",
        installation_id, consented,
    )
```

Note `get_installation_by_token_hash` (`db.py`, ~line 1317) uses an **explicit column list**
(`SELECT i.installation_id, i.account_login, i.plan`) — adding the new column to the table does
**not** make it available through that function. Do not modify that function (it's used broadly
for auth elsewhere, changing its shape is out of scope for this task). Add a small, separate
`async def get_free_tier_ai_consent(pool, installation_id) -> bool | None` that selects just that
one column.

### 2. Per-key daily quota tracking

New module `github-app/scan_worker/free_tier_pool.py`. Track cumulative **token** usage per key
per UTC day (the free-quota programs are token-budgeted, not request-count-budgeted), using
`rate_limit.py`'s `is_rate_limited` INCR/EXPIRE-NX shape as the reference, but for token counts,
not request counts, and with the actual token usage reported *after* a real call completes (from
the OpenAI-compatible response's `usage.total_tokens`), not estimated beforehand:

```python
def record_key_usage(redis_conn, key_id: str, tokens_used: int) -> None:
    """Adds tokens_used to today's (UTC) running total for this key."""
    ...  # INCRBY on a `free-tier-usage:{key_id}:{utc_date}` key, EXPIRE NX at ~25h

def key_has_budget(redis_conn, key_id: str, daily_token_budget: int) -> bool:
    """False once today's recorded usage for this key would exceed its budget."""
    ...
```

### 3. The pool

```python
FREE_TIER_OPENAI_MODEL = "gpt-5.4-nano"
FREE_TIER_GEMINI_MODEL = "gemini-3.5-flash-lite"

# Real published free-tier budgets - see the OpenAI Data Controls "Share inputs and
# outputs" screen and Google AI Studio's own rate-limit docs for the authoritative
# current numbers; confirm both before hardcoding, they change.
FREE_TIER_OPENAI_DAILY_TOKEN_BUDGET = 2_500_000  # gpt-5.4-nano is in the nano/mini free tier
FREE_TIER_GEMINI_DAILY_TOKEN_BUDGET = ...  # look this up - AI Studio limits are RPD/TPM based, not identical in shape to OpenAI's; report what you actually find, don't assume it matches OpenAI's shape

# Environment variable naming: OPENAI_FREE_TIER_KEY_1, OPENAI_FREE_TIER_KEY_2,
# GEMINI_FREE_TIER_KEY_1..4. Missing keys are skipped (pool just has fewer members), never
# a hard failure - this must degrade gracefully if the user hasn't provisioned all 6 yet.


class FreeTierPoolAdapter:
    """Exposes the same simple_completion(...) interface every writing_adapter_for(...)
    caller already expects. Picks the first key (checking OpenAI's pool before Gemini's,
    since gpt-5.4-nano scored higher on every Artificial Analysis dimension in the real
    comparison this decision was made from) that still has budget today; raises a clear,
    specific exception if the entire pool is exhausted, so the caller can degrade instead
    of getting a confusing raw API error."""
    ...


class FreeTierPoolExhaustedError(Exception):
    """The entire free-tier pool (all 6 keys, both providers) is out of daily budget.
    Callers must catch this and fall back to the pre-existing BYOK/local path - this is
    not a crash, it's an expected, planned-for daily state once free-tier usage grows."""
```

### 4. Wire into `model_for_plan` / `writing_adapter_for_plan`

In `model_tiers.py`:

```python
def model_for_plan(plan: str) -> str:
    if plan == "free":
        return FREE_TIER_OPENAI_MODEL  # or whichever the pool actually picks - see note below
    return resolve_model(PRO_MODEL)


def writing_adapter_for_plan(plan, on_usage=None, before_llm_call=None, allow_partial_report=False):
    if plan == "free":
        return free_tier_pool.get_adapter(on_usage=on_usage)  # raises FreeTierPoolExhaustedError if empty
    return writing_adapter_for(PRO_MODEL, on_usage=on_usage, before_llm_call=before_llm_call, allow_partial_report=allow_partial_report)
```

**This function does not check consent.** Every one of the six existing callers in `jobs.py`
already has `plan` in scope from the installation record — it is each *caller's* job to check
`free_tier_ai_consent` before ever calling `writing_adapter_for_plan("free", ...)` in the first
place, and to fall back (BYOK/local, or a clear "free hosted AI requires opting in - see docs"
message) when consent is `NULL` or `false`. **Do not wire the six existing `jobs.py` call sites to
actually pass `plan="free"` in this task** — that consent-checking + fallback logic at each call
site is real, separate work with its own review surface (see Explicitly out of scope, below).
This phase only makes `model_for_plan`/`writing_adapter_for_plan` *capable* of serving free-tier
traffic when a future caller opts in; it does not yet turn that on anywhere.

### 5. Tests

- Real (not mocked) test of the OpenAI free-tier key against `api.openai.com` with
  `gpt-5.4-nano` — requires a real key in the environment; skip cleanly (like existing tests
  that need Postgres/Redis do — `pytest.skip` pattern) if `OPENAI_FREE_TIER_KEY_1` isn't set,
  don't fail the suite when it's absent.
- Same for one real Gemini key against the (hopefully-reused) `OpenAICompatibleAdapter`.
- `FreeTierPoolAdapter` unit tests with fake `redis_conn` (the `redis_conn` pytest fixture
  already exists — see `github-app/tests/test_rate_limit.py` for the pattern): picks a key
  with budget, skips an exhausted key, raises `FreeTierPoolExhaustedError` when all 6 are
  exhausted.
- `model_for_plan("free")` / `writing_adapter_for_plan("free", ...)` return the free-tier
  path; `model_for_plan("air")` / other plans are completely unchanged (regression-test the
  existing paid behavior wasn't touched).

## Explicitly out of scope for this pass (do not build these)

- Wiring any of the six existing `jobs.py` call sites to actually pass `plan="free"` and check
  consent before doing so. That's Phase 2, reviewed separately, once this phase's pool is
  verified real and solid.
- Flash Review / PR-review integration specifically (`_run_flash_review` in `jobs.py` currently
  gates hosted embeddings to paid plans entirely via a 402 in `embeddings_api.py` — extending PR
  review itself to free-tier is a bigger, separate decision with its own consent-UX design, not
  something to fold into this pass).
- The CLI-side consent prompt UX (mirroring `_default_confirm_openai_fallback`'s pattern, but
  this is a new prompt with new copy that needs the exact disclosure language reviewed before
  shipping - don't write the actual user-facing prompt text yet, that's a separate, deliberate
  piece).
- Any change to `has_api_key`/`get_api_key`/credential-file handling in `src/aletheore/credentials.py`.
- Rewriting `get_installation_by_token_hash` or any other existing auth path's column list.

## What the reviewer (Claude) will check

- `plan == "free"` in `model_for_plan`/`writing_adapter_for_plan` is the *only* change to those
  two functions' existing behavior for every other plan value — diff them against current
  `model_tiers.py` and confirm nothing else moved.
- No `jobs.py` call site was changed to pass `plan="free"` — that's explicitly out of scope here.
- `FreeTierPoolExhaustedError` is a real, catchable, specific exception — not a generic
  `Exception` or a silent `None` return that a caller could mistake for success.
- The real API test against at least one real OpenAI free-tier key and one real Gemini key
  actually ran and is reported with real output — same bar as the blast-radius spec's real-data
  verification requirement.
- Every one of the 6 key environment variables is optional (pool degrades gracefully with fewer
  keys), confirmed by a test that only sets 1-2 of the 6 and verifies no crash.
- No hardcoded API keys, no keys logged, no keys in test fixtures beyond an obviously-fake
  placeholder string for the mocked-Redis tests.
