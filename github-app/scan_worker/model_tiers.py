"""Which LLM Aletheore's paid AI work actually runs on - the pricing
page's model claims are read from this file, not written separately, so
they can never drift from what actually runs.

GPT-5.6 Luna is the primary model for every writing surface (managed
audits, one-time AIRview/Docs builds, PR reviews, and AIRview/Docs
incremental updates) as of 2026-08-09: DeepSeek V4 Flash wasn't catching
enough real issues on PR review specifically, and DeepSeek's announced
"significant" price hike (2x-10x per their own founder, size and date
still unconfirmed) made staying DeepSeek-only a real vendor-risk bet, not
just a cost one. Luna's own coding/intelligence/agentic benchmarks
(Artificial Analysis, via OpenRouter's compare page) beat DeepSeek V4
Pro's while pricing under it.

Falls back to the pre-existing DeepSeek model for that surface if
OPENAI_API_KEY isn't configured yet, so a build never hard-fails on
missing infra - logged, never silent, so this is never mistaken for the
intended path. (The retired gpt-5.6-terra rollout crashed instead of
falling back, but only because its price entry was missing from
llm_cost.py, not because it lacked a fallback path - gpt-5.6-luna has a
price entry from the start.)
"""

import logging
import os
from datetime import datetime, timezone
from typing import Callable

from aletheore.adapters.openai_compatible import OpenAICompatibleAdapter
from aletheore.credentials import has_api_key

# Real free daily allowance, not an abuse ceiling: gpt-5-nano falls in
# OpenAI's shared-traffic free-tier bucket (gpt-5.4-mini/nano, gpt-5-mini,
# gpt-5-nano, gpt-4.1-mini/nano, gpt-4o-mini, o3-mini, o4-mini), which gets
# 2,500,000 free tokens PER DAY, not per month (confirmed against OpenAI's
# own published free-tier terms - usage above this is billed at standard
# rates, which this key should never actually reach). Stopped 100,000
# tokens short of that as a real safety margin, not cut exactly at the
# edge - usage is checked once per Flash Review, not per token, so the
# last review before the cap trips could still land close to it.
OPENAI_FREE_TIER_DAILY_TOKEN_CAP = 2_400_000

# Conservative per-review reservation, anchored to this project's own
# measured worst-case Flash Review prompt+completion size after the
# context-depth caps were doubled (~112,000 tokens, see
# docs/superpowers/specs/2026-08-18-flash-review-context-depth-increase.md),
# rounded up for margin. Free-tier reviews share the exact same context-
# building code as paid tier, so they can be just as large.
OPENAI_FREE_TIER_RESERVATION_TOKENS = 130_000


def _openai_free_tier_token_key() -> str:
    # Scoped to calendar day (UTC) - the allowance itself resets daily.
    return f"free_tier:openai_tokens:{datetime.now(timezone.utc):%Y-%m-%d}"


def openai_free_tier_tokens_today(redis_conn) -> int:
    value = redis_conn.get(_openai_free_tier_token_key())
    return int(value) if value is not None else 0


def _reserve_openai_free_tier_budget(redis_conn) -> bool:
    """Atomically reserve OPENAI_FREE_TIER_RESERVATION_TOKENS against
    today's counter, right before a real OpenAI call is about to happen
    (wired as an adapter's before_llm_call - invoked fresh per real
    attempt, never at chain-build time). Returns True if the reservation
    fit under the cap and the call may proceed; False if it didn't, in
    which case the reservation is released immediately and the caller
    (openai_compatible.OpenAICompatibleAdapter._ensure_budget_for_next_call)
    raises AdapterInvocationError, which run_with_free_tier_fallback
    already treats as "try the next provider" - no separate handling
    needed here.

    This closes two real gaps a plain read-then-decide check had: (1) two
    concurrent free-tier reviews could both read the counter as under-cap
    before either had recorded real usage - the reservation itself is one
    atomic INCRBY, so the worst-case overshoot across concurrent callers
    is bounded by one reservation each, not unbounded; (2) an adapter that
    was merely *included* in the chain but never actually reached (an
    earlier provider succeeded first) never reserves anything, since this
    only fires at the moment a real call is about to be attempted."""
    key = _openai_free_tier_token_key()
    new_total = redis_conn.incrby(key, OPENAI_FREE_TIER_RESERVATION_TOKENS)
    if hasattr(redis_conn, "expire"):
        # 2 days: comfortably outlives the single calendar day this key is
        # scoped to (covers timezone-boundary edge cases), so it cleans
        # itself up without ever needing a cron.
        redis_conn.expire(key, 2 * 24 * 3600)
    if new_total > OPENAI_FREE_TIER_DAILY_TOKEN_CAP:
        redis_conn.incrby(key, -OPENAI_FREE_TIER_RESERVATION_TOKENS)
        return False
    return True


def _true_up_openai_free_tier_reservation(redis_conn, real_total_tokens: int) -> None:
    """Correct the reservation placeholder with the real prompt+completion
    total once a reserved call has actually completed - the reservation
    was a conservative estimate, not the real usage."""
    delta = real_total_tokens - OPENAI_FREE_TIER_RESERVATION_TOKENS
    if delta != 0:
        redis_conn.incrby(_openai_free_tier_token_key(), delta)

LUNA_MODEL = "gpt-5.6-luna"
PRO_MODEL = "deepseek-v4-pro"
VERIFICATION_MODEL = "deepseek-v4-flash"

# Every model we write with is a reasoning model, and reasoning tokens are
# billed as output tokens - the most expensive kind. Nothing was switching them
# off, so AIRview has been paying for discarded chain-of-thought on every page.
# Measured on deepseek-v4-flash: a 40-page build emitted 1.93M output tokens
# across ~50 calls, ~38,000 per call, for pages the prompt caps at 250-400 words
# (~600 tokens). A probe asking only for the word "ok" returned 17 completion
# tokens of which 15 were reasoning_tokens.
#
# The parameter differs per provider and the intuitive value is wrong on
# DeepSeek: reasoning_effort "minimal" and "low" measured WORSE than the default
# (45 and 64 reasoning tokens against 13). Only the explicit disable reaches
# zero, verified against the live API for both spellings below.
#
# NOT enabled by default, because it was measured and it costs quality. On the
# AutoMapper comprehension arm, disabling thinking scored 1.15 against 1.50 with
# it on (-0.35, at the judge's 0.38 noise floor), corroborated by two mechanical
# signals: pages came back 46% shorter (3,491 vs 5,104 chars) and one fewer page
# survived citation verification. The saving is real - ~10x cheaper, ~6x faster -
# but AIRview quality is the product, so this is a deliberate trade, not a free
# win, and it is off until someone chooses it.
#
# Measured on DeepSeek only. OpenAI exposes a 7-rung ladder (none/minimal/low/
# medium/high/xhigh/max) where DeepSeek is effectively binary, so an intermediate
# rung on Luna - the model production actually writes with - may keep the quality
# and most of the saving. That experiment is the reason this stays wired up.
#
# Set AIRVIEW_REASONING=off to apply it.
NO_THINKING_OPENAI = {"reasoning_effort": "none"}
NO_THINKING_DEEPSEEK = {"thinking": {"type": "disabled"}}


def _reasoning_body(disabled_value: dict) -> dict | None:
    """The extra_body for this provider, or None to leave the model's default."""
    return disabled_value if os.environ.get("AIRVIEW_REASONING") == "off" else None


def _openai_available() -> bool:
    return has_api_key("OPENAI_API_KEY", "OpenAI")


def resolve_model(fallback_model: str) -> str:
    """The model name writing_adapter_for(fallback_model, ...) will
    actually construct right now - used for cost accounting and cache
    labeling, so a spend cap or cached result is never silently mispriced
    or mislabeled against a model that isn't the one that actually ran.
    """
    return LUNA_MODEL if _openai_available() else fallback_model


def writing_adapter_for(
    fallback_model: str,
    on_usage: Callable[[int, int, int], None] | None = None,
    before_llm_call: Callable[[], bool] | None = None,
    allow_partial_report: bool = False,
    _prefer_luna: bool = True,
) -> OpenAICompatibleAdapter:
    if _prefer_luna and _openai_available():
        return OpenAICompatibleAdapter(
            name="OpenAI",
            base_url="https://api.openai.com/v1",
            api_key_env_var="OPENAI_API_KEY",
            model=LUNA_MODEL,
            extra_body=_reasoning_body(NO_THINKING_OPENAI),
            on_usage=on_usage,
            before_llm_call=before_llm_call,
            allow_partial_report=allow_partial_report,
        )
    if not _prefer_luna:
        logging.getLogger(__name__).info(
            "using DeepSeek (%s) for this writing surface by explicit preference, "
            "not an OpenAI fallback", fallback_model,
        )
    else:
        logging.getLogger(__name__).warning(
            "OPENAI_API_KEY not configured - falling back to DeepSeek (%s)", fallback_model
        )
    return OpenAICompatibleAdapter(
        name="DeepSeek",
        base_url="https://api.deepseek.com",
        api_key_env_var="DEEPSEEK_API_KEY",
        model=fallback_model,
        # deepseek-v4-pro runs in thinking mode by default, which rejects
        # tool_choice="required" (400 invalid_request_error) - fall back to
        # the same unforced tool-choice path used for Ollama. Harmless for
        # callers that only use simple_completion(), which never sets this.
        supports_tool_choice=False,
        extra_body=_reasoning_body(NO_THINKING_DEEPSEEK),
        on_usage=on_usage,
        before_llm_call=before_llm_call,
        allow_partial_report=allow_partial_report,
    )


def writing_adapter_for_airview(
    fallback_model: str,
    on_usage: Callable[[int, int, int], None] | None = None,
    before_llm_call: Callable[[], bool] | None = None,
) -> OpenAICompatibleAdapter:
    """Always DeepSeek for AIRview specifically - never Luna, regardless of
    OPENAI_API_KEY availability.

    Every other writing surface prefers Luna over DeepSeek when available
    (writing_adapter_for above) because Luna measured better on real-world
    coding/PR-review benchmarks - that is still true and unchanged here.
    AIRview's own comprehension benchmark (aletheore-benchmarks,
    AIRVIEW_GAP.md) measured the opposite for this one surface: the full
    12-question architecture set, 3 judge repeats, deepseek-v4-flash scored
    1.88 against RepoWise's 1.99 (a statistical tie, inside the judge's own
    noise floor) while gpt-5.6-luna scored 1.53 against RepoWise's 2.08 (a
    real loss, outside it) - same corpus, same day, same rubric. Scoped
    narrowly to AIRview because that is exactly what was measured; PR
    review was not re-tested and stays on Luna via writing_adapter_for/
    writing_adapter_for_plan. Managed audits moved off Luna separately -
    see writing_adapter_for_managed_audit below.
    """
    return writing_adapter_for(
        fallback_model, on_usage=on_usage, before_llm_call=before_llm_call, _prefer_luna=False
    )


# Not resolve_model(PRO_MODEL) or any other dynamic choice - always exactly
# this one model, unconditionally. See writing_adapter_for_managed_audit's
# docstring for the real numbers behind why.
MANAGED_AUDIT_MODEL = "deepseek-v4-flash"


def writing_adapter_for_managed_audit(
    on_usage: Callable[[int, int, int], None] | None = None,
    before_llm_call: Callable[[], bool] | None = None,
    allow_partial_report: bool = False,
) -> OpenAICompatibleAdapter:
    """Always DeepSeek Flash for managed_audit specifically - never Luna
    (writing_adapter_for_plan's default) and never DeepSeek Pro either.

    Measured directly, three real full audit runs against this repository,
    same evidence, same manual: Luna cost $0.15 (6 rounds) and missed a
    real circular import; deepseek-v4-pro cost $1.15 (14 rounds) and caught
    it; deepseek-v4-flash cost $0.40 (16 rounds) and also caught it. Pro's
    3x-higher per-token rate over flash bought nothing here - pro actually
    used fewer total tokens than flash, so the extra cost was pure list-
    price premium, not more work done, for a shorter report and the
    identical finding. Flash is the only one of the three that is both
    accurate (matches Pro's finding) and cheap (a fraction of Pro's cost)
    for this specific task.

    This doesn't generalize from AIRview's own Luna-vs-DeepSeek finding
    above (or the other direction, Luna-preferred by default elsewhere):
    managed_audit is multi-round agentic tool use, not a single completion,
    and its cost is ~96% input-token-driven because every round re-sends
    the entire accumulated conversation - round-trip efficiency dominates
    over any model's per-token list price, which is exactly what made Pro
    the expensive choice here despite its higher-tier positioning.
    """
    return writing_adapter_for(
        MANAGED_AUDIT_MODEL,
        on_usage=on_usage,
        before_llm_call=before_llm_call,
        allow_partial_report=allow_partial_report,
        _prefer_luna=False,
    )


def model_for_plan(plan: str) -> str:
    return resolve_model(PRO_MODEL)


def writing_adapter_for_plan(
    plan: str,
    on_usage: Callable[[int, int, int], None] | None = None,
    before_llm_call: Callable[[], bool] | None = None,
    allow_partial_report: bool = False,
) -> OpenAICompatibleAdapter:
    return writing_adapter_for(
        PRO_MODEL,
        on_usage=on_usage,
        before_llm_call=before_llm_call,
        allow_partial_report=allow_partial_report,
    )


def verification_adapter(
    on_usage: Callable[[int, int, int], None] | None = None,
) -> OpenAICompatibleAdapter:
    """Always DeepSeek V4 Flash, regardless of whether OpenAI is configured -
    unlike writing_adapter_for, which prefers OpenAI when available and only
    falls back to DeepSeek when it isn't. Independent verification only means
    something if the checking model didn't also write the finding, so this
    must not follow generation's own preference the way writing_adapter_for
    does.
    """
    return OpenAICompatibleAdapter(
        name="DeepSeek",
        base_url="https://api.deepseek.com",
        api_key_env_var="DEEPSEEK_API_KEY",
        model=VERIFICATION_MODEL,
        # See writing_adapter_for's DeepSeek branch - deepseek-v4-pro runs in
        # thinking mode by default and rejects tool_choice="required", but
        # this only matters for a model still forced onto thinking; kept
        # here for the same reason it's kept there, not because flash-tier
        # is known to need it.
        supports_tool_choice=False,
        on_usage=on_usage,
    )


def writing_adapter_chain_for_free_tier(
    redis_conn,
    on_usage: Callable[[int, int, int], None] | None = None,
) -> list[OpenAICompatibleAdapter]:
    """Build one OpenAICompatibleAdapter per free-tier provider whose env var
    is configured, in fallback priority order: Groq, Gemini, OpenAI free-tier
    key, OpenRouter last. Providers whose key is missing are silently skipped
    (never hard-fail on missing infra). If the list ends up empty, callers
    should behave like today: no free-tier Flash Review.

    redis_conn is required (not optional) - it backs the real daily token
    cap on the OpenAI free-tier key below, which is a real allowance
    boundary, not an abuse ceiling, and must never be silently skippable by
    omitting it."""
    logger = logging.getLogger(__name__)
    chain: list[OpenAICompatibleAdapter] = []

    if has_api_key("GROQ_API_KEY", "Groq"):
        chain.append(OpenAICompatibleAdapter(
            name="Groq",
            base_url="https://api.groq.com/openai/v1",
            api_key_env_var="GROQ_API_KEY",
            model="openai/gpt-oss-120b",
            on_usage=on_usage,
        ))
    else:
        logger.info("free-tier: GROQ_API_KEY not configured, skipping Groq")

    if has_api_key("GEMINI_API_KEY", "Gemini"):
        chain.append(OpenAICompatibleAdapter(
            name="Gemini",
            base_url="https://generativelanguage.googleapis.com/v1beta/openai",
            api_key_env_var="GEMINI_API_KEY",
            model="gemini-3.5-flash",
            on_usage=on_usage,
        ))
    else:
        logger.info("free-tier: GEMINI_API_KEY not configured, skipping Gemini")

    if has_api_key("OPENAI_FREE_TIER_API_KEY", "OpenAI-FreeTier"):
        def _on_openai_free_tier_usage(
            prompt_tokens: int, completion_tokens: int, cached_tokens: int = 0
        ) -> None:
            _true_up_openai_free_tier_reservation(redis_conn, prompt_tokens + completion_tokens)
            if on_usage is not None:
                on_usage(prompt_tokens, completion_tokens, cached_tokens)

        # The daily cap is enforced via before_llm_call, not by deciding
        # here whether to include this adapter - see
        # _reserve_openai_free_tier_budget's docstring for why: this
        # closes a real TOCTOU race (concurrent reviews both reading the
        # counter as under-cap before either recorded usage) that a
        # plain check-then-include here could not.
        chain.append(OpenAICompatibleAdapter(
            name="OpenAI-FreeTier",
            base_url="https://api.openai.com/v1",
            api_key_env_var="OPENAI_FREE_TIER_API_KEY",
            model="gpt-5-nano",
            extra_body={"reasoning_effort": "minimal"},
            on_usage=_on_openai_free_tier_usage,
            before_llm_call=lambda: _reserve_openai_free_tier_budget(redis_conn),
            # Releases the reservation before_llm_call just made when the
            # real call then fails (rate limit, auth error, timeout) -
            # on_usage never fires on a failed call, so without this the
            # reservation is permanently stuck against a call that used zero
            # real tokens. See openai_compatible.OpenAICompatibleAdapter's
            # on_call_failed for why this can't just reuse on_usage(0, 0):
            # that would misrepresent a failed call as a completed one to
            # any other on_usage consumer.
            on_call_failed=lambda: _true_up_openai_free_tier_reservation(redis_conn, 0),
            # OpenAICompatibleAdapter's default budget_exceeded_message
            # names the monthly LLM spend cap - correct for every other
            # before_llm_call wiring (e.g. jobs.py's spend_budget.
            # can_start_next_call), but wrong here: this adapter's
            # before_llm_call is the daily free-tier token allowance, a
            # different cap entirely. Left at the default, an ops alert for
            # a healthy daily rollover reads as a billing problem.
            budget_exceeded_message="the daily free-tier token allowance would be exceeded",
        ))
    else:
        logger.info("free-tier: OPENAI_FREE_TIER_API_KEY not configured, skipping OpenAI free-tier")

    if has_api_key("OPENROUTER_API_KEY", "OpenRouter"):
        chain.append(OpenAICompatibleAdapter(
            name="OpenRouter",
            base_url="https://openrouter.ai/api/v1",
            api_key_env_var="OPENROUTER_API_KEY",
            model="nvidia/nemotron-3.5-lightning:free",
            on_usage=on_usage,
        ))
    else:
        logger.info("free-tier: OPENROUTER_API_KEY not configured, skipping OpenRouter")

    return chain


class FreeTierFallbackExhausted(Exception):
    """Raised when every adapter in the free-tier chain has failed."""

    def __init__(self, errors: list[tuple[str, Exception]]):
        self.errors = errors
        names = ", ".join(name for name, _ in errors)
        super().__init__(f"All free-tier providers failed: {names}")


def run_with_free_tier_fallback(
    adapters: list[OpenAICompatibleAdapter],
    fn: Callable[[OpenAICompatibleAdapter], str],
) -> str:
    """Try each adapter in the chain in order. `fn(adapter)` is called with
    each adapter; if it raises (rate limit / 429, timeout, 5xx, auth failure),
    log the failure and move to the next adapter. Only raise
    FreeTierFallbackExhausted if every adapter fails. Log which provider
    actually served the successful request."""
    logger = logging.getLogger(__name__)
    errors: list[tuple[str, Exception]] = []

    for adapter in adapters:
        try:
            result = fn(adapter)
            logger.info("free-tier: %s served request successfully", adapter.name)
            return result
        except Exception as exc:  # noqa: BLE001
            logger.warning("free-tier: %s failed (%s: %s), trying next provider", adapter.name, type(exc).__name__, exc)
            errors.append((adapter.name, exc))

    raise FreeTierFallbackExhausted(errors)
