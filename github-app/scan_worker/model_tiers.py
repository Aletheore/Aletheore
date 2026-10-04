"""Which LLM Aletheore's paid AI work actually runs on - the pricing
page's model claims are read from this file, not written separately, so
they can never drift from what actually runs.

GPT-5.6 Luna was the primary model for every writing surface as of
2026-08-09 (see git history for the full reasoning). As of 2026-10-04,
AIRview, managed audits, Docs, and endpoint-health fix suggestions moved
to IndieRouter as the primary provider (`writing_adapter_for_airview`,
`writing_adapter_for_managed_audit`, `writing_adapter_for_docs`,
`writing_adapter_for_health_fix_suggestion` below) - one balance, one
account, one privacy disclosure, and measured parity-or-better on every
surface (docs/operations/LLM-CONSOLIDATION-HANDOVER-2026-10-03.md, local-
only). PR review (`flash_review_generation_adapter`) was already on
IndieRouter before this. The plain `writing_adapter_for` below (still used
by `writing_adapter_for_plan`/Docs' fallback branch) keeps its original
Luna-preferred behavior unchanged, for whichever surface or fallback path
still reaches it.

Every IndieRouter-primary builder falls back to its own pre-existing
direct-provider path, unchanged, if INDIEROUTER_API_KEY isn't configured -
logged, never silent, so a build never hard-fails on missing infra and
this is never mistaken for the intended path. (The retired gpt-5.6-terra
rollout crashed instead of falling back, but only because its price entry
was missing from llm_cost.py, not because it lacked a fallback path -
every model here needs a llm_cost.py entry before its first real call.)
"""

import logging
import os
import threading
from datetime import datetime, timezone
from typing import Callable

from aletheore.adapters.openai_compatible import (
    MANAGED_AUDIT_MAX_TOOL_ROUNDS,
    REQUEST_TIMEOUT_SECONDS,
    OpenAICompatibleAdapter,
)
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


def _reserve_openai_free_tier_budget(redis_conn, key: str | None = None) -> bool:
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
    only fires at the moment a real call is about to be attempted.

    `key`: the caller may pass the exact key to reserve against (see
    run_with_free_tier_fallback's chain-building, which captures this
    same key for the later true-up/release call) - defaults to computing
    a fresh one, preserving this function's own standalone behavior for
    any other caller. Checked against None, not truthiness - Flash
    Review finding: `key or ...` would silently discard a caller-supplied
    empty string and reserve against a freshly computed key instead,
    contradicting this docstring's own "the exact key to use" contract.
    """
    key = key if key is not None else _openai_free_tier_token_key()
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


def _true_up_openai_free_tier_reservation(
    redis_conn, real_total_tokens: int, key: str | None = None
) -> None:
    """Correct the reservation placeholder with the real prompt+completion
    total once a reserved call has actually completed - the reservation
    was a conservative estimate, not the real usage.

    `key`: the exact key the matching reservation was placed against (see
    _reserve_openai_free_tier_budget's own `key` parameter) - real bug
    this closes: computing a fresh key here independently, rather than
    reusing the one the reservation actually used, meant a call that
    straddled the UTC midnight boundary between reservation and true-up
    corrected the WRONG day's counter - permanently over-reserving the
    day it actually ran on (bounded by the key's own 2-day TTL) and
    leaking negative headroom into the next day's real allowance.
    Defaults to computing a fresh key, preserving this function's own
    standalone behavior for any other caller. Checked against None, not
    truthiness, for the same reason _reserve_openai_free_tier_budget's
    own `key` parameter is - see its docstring."""
    delta = real_total_tokens - OPENAI_FREE_TIER_RESERVATION_TOKENS
    if delta != 0:
        redis_conn.incrby(key if key is not None else _openai_free_tier_token_key(), delta)

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
    on_call_failed: Callable[[], None] | None = None,
    allow_partial_report: bool = False,
    _prefer_luna: bool = True,
    json_output: bool = False,
    max_tool_rounds: int | None = None,
    request_timeout_seconds: int | None = None,
) -> OpenAICompatibleAdapter:
    """json_output: the caller's completions are parsed as JSON (AIRview and
    Docs writing). Applied to the OpenAI model only, where long responses
    come back malformed often enough to drop whole batches; the DeepSeek
    path is left exactly as it was.

    max_tool_rounds: only meaningful for a caller whose adapter is used via
    .invoke() (managed_audit - see writing_adapter_for_managed_audit), not
    simple_completion(). request_timeout_seconds: a per-call override (see
    AIRVIEW_REQUEST_TIMEOUT_SECONDS/MANAGED_AUDIT_REQUEST_TIMEOUT_SECONDS).
    Both None leave OpenAICompatibleAdapter's own defaults (MAX_TOOL_ROUNDS/
    REQUEST_TIMEOUT_SECONDS) in place, so every writing surface that
    doesn't pass them is unaffected."""
    override_kwargs = {}
    if max_tool_rounds is not None:
        override_kwargs["max_tool_rounds"] = max_tool_rounds
    if request_timeout_seconds is not None:
        override_kwargs["request_timeout_seconds"] = request_timeout_seconds
    if _prefer_luna and _openai_available():
        return OpenAICompatibleAdapter(
            name="OpenAI",
            base_url="https://api.openai.com/v1",
            api_key_env_var="OPENAI_API_KEY",
            model=LUNA_MODEL,
            extra_body=_reasoning_body(NO_THINKING_OPENAI),
            on_usage=on_usage,
            before_llm_call=before_llm_call,
            on_call_failed=on_call_failed,
            allow_partial_report=allow_partial_report,
            json_mode=json_output,
            **override_kwargs,
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
        on_call_failed=on_call_failed,
        allow_partial_report=allow_partial_report,
        **override_kwargs,
    )


CROSS_FILE_CHECK_MODEL = "gpt-6-luna"


def cross_file_check_adapter(
    on_usage: Callable[[int, int, int], None] | None = None,
) -> OpenAICompatibleAdapter:
    """The model for flash_review._check_findings_against_whole_diff. gpt-6-luna at
    reasoning_effort=low, chosen by measurement, not price alone: on the 13-case
    real-PR corpus it caught 9 confirmed false positives with zero true positives or
    golden catches lost, at ~$0.001/PR. The same two candidate models that were
    tried as the checker behaved very differently - deepseek-v4-flash dropped 5
    golden-bug catches and 4 true positives while catching only 3 false positives,
    so this is deliberately not verification_adapter().

    Never the generator (GLM via IndieRouter), so it can't be checking its own work.
    Needs OPENAI_API_KEY; without it the check is skipped and every finding stands.
    """
    return OpenAICompatibleAdapter(
        name="OpenAI",
        base_url="https://api.openai.com/v1",
        api_key_env_var="OPENAI_API_KEY",
        model=CROSS_FILE_CHECK_MODEL,
        extra_body={"reasoning_effort": "low"},
        on_usage=on_usage,
    )


FLASH_REVIEW_GENERATION_MODEL = "glm-5.3-flash"


def _indierouter_available() -> bool:
    return has_api_key("INDIEROUTER_API_KEY", "IndieRouter")


def indierouter_available() -> bool:
    """Public wrapper of _indierouter_available for callers outside this
    module (live_wiki's worker-count/batch-size sizing) that need the same
    capability check without reaching into a private name."""
    return _indierouter_available()


# IndieRouter's full paid-tier catalogue (2026-10-03) is exactly two
# models: glm-5.3-flash (FLASH_REVIEW_GENERATION_MODEL above, and
# HEALTH_FIX_SUGGESTION_MODEL below) and this one. Never "deepseek-v4-flash"
# here - that alias is IndieRouter-catalogue-only and is removed from
# IndieRouter entirely on 2026-10-15. "deepseek-v4-flash" stays correct
# ONLY as a direct-DeepSeek-API fallback model id, a different base_url -
# see llm_cost.py's own comment on why the two need separate price entries.
INDIEROUTER_DEEPSEEK_MODEL = "deepseek-v4.1-flash"


def _indierouter_adapter(
    model: str,
    *,
    on_usage: Callable[[int, int, int], None] | None = None,
    before_llm_call: Callable[[], bool] | None = None,
    on_call_failed: Callable[[], None] | None = None,
    allow_partial_report: bool = False,
    reasoning_effort: str | None = None,
    json_mode: bool = False,
    request_timeout_seconds: int = REQUEST_TIMEOUT_SECONDS,
    max_tool_rounds: int | None = None,
) -> OpenAICompatibleAdapter:
    """Shared constructor for every IndieRouter-primary builder below
    (AIRview, Docs, managed audits, health-fix suggestions) - one place
    fixing base_url/api_key_env_var so a typo in one surface's wiring
    can't diverge from another's. Deliberately NOT used by
    flash_review_generation_adapter above (out of scope for this change,
    already correct) - a little duplication against that one is accepted
    on purpose rather than touching an already-production-proven path.

    max_tool_rounds: only meaningful for managed audits (the only
    .invoke()-based caller here) - None leaves OpenAICompatibleAdapter's
    own default (openai_compatible.MAX_TOOL_ROUNDS) in place."""
    return OpenAICompatibleAdapter(
        name="IndieRouter",
        base_url="https://api.indierouter.ai/v1",
        api_key_env_var="INDIEROUTER_API_KEY",
        model=model,
        extra_body={"reasoning_effort": reasoning_effort} if reasoning_effort else None,
        json_mode=json_mode,
        request_timeout_seconds=request_timeout_seconds,
        on_usage=on_usage,
        before_llm_call=before_llm_call,
        on_call_failed=on_call_failed,
        **({} if max_tool_rounds is None else {"max_tool_rounds": max_tool_rounds}),
        allow_partial_report=allow_partial_report,
    )


def flash_review_generation_adapter(
    on_usage: Callable[[int, int, int], None] | None = None,
    before_llm_call: Callable[[], bool] | None = None,
    fallback_model: str = "deepseek-v4-flash",
) -> OpenAICompatibleAdapter:
    """Flash Review's real generation adapter - GLM-5.3-Flash via
    IndieRouter, PR-Agent's own real system/user prompt (see
    flash_review.py's FLASH_REVIEW_SYSTEM_PROMPT/_FLASH_REVIEW_USER_PROMPT_TEMPLATE),
    temperature=0.2, reasoning_effort=low.

    The permanent validation record: PR-Agent's real prompt + 6 condensed
    Aletheore safety rules appended AFTER PR-Agent's own schema/example
    block (not through its extra_instructions slot, which measured worse -
    55.2% avg F1, a real regression from adding anything there at all) +
    temperature=0.2 + reasoning_effort=low, run 3x on the FULL 50-PR
    Martian corpus across 5 real repos (sentry/grafana/cal.com/discourse/
    keycloak, 10 each, not just one repo's subset) against golden review
    comments, gpt-5-nano judge: F1=60.4% avg (56.3-62.6% range) - the
    number that actually gates this decision. The 6 rules: untrusted-
    content/prompt-injection defense, referenced-symbol evidence-only
    claims, swallowed-exception-despite-comment override, sibling-
    consistency check (equals/hashCode, serialize/deserialize, etc.),
    behavior-changed-before-reporting comparison, and diff-hunk-header-
    is-not-proof-of-nesting. Two OTHER candidate rules (a local-logic
    sanity check, a host-language-escaping warning) were tried and
    REJECTED: on the same full-corpus protocol they measured 61.7% avg
    with a much wider, noisier spread (49.1-71.7%) when tested alongside
    a since-superseded 4-rule baseline - a real, reproducible cost, unlike
    the 2 rules that shipped, which cost nothing measurable. A bare-4-rule
    version (without the 2 behavior/hunk-header rules) scored 60.0% avg
    (57.9-62.1%) on this same full-corpus protocol - the two are
    statistically indistinguishable, so the 6-rule version shipped for its
    extra real bug-class coverage at no measured cost, not because it
    scored higher. A keycloak-only 10-PR pilot of the 4-rule config scored
    higher (66.3% avg) and an even earlier, bare-PR-Agent-prompt-with-no-
    safety-rules keycloak-only pilot scored 62.4% avg (8 runs) - both real
    numbers from real runs, but both from the smaller, single-repo subset
    that consistently overstated effects relative to the full corpus all
    night; cited here only for provenance, not as numbers that justified
    shipping.
    temperature=0.2 is PR-Agent's own real production default (their
    configuration.toml), never previously set anywhere in this codebase.
    reasoning_effort=low is a separate, independently validated lever
    specific to GLM-5.3 (its thinking mode cannot be disabled, only
    steered - "low" measured the same or better quality than the default
    effort while avoiding the real timeouts "high"/"max" hit on longer
    prompts) - unrelated to prompt wording, kept regardless of provider.

    Sarvam was evaluated side-by-side as an alternative host for the same
    model and rejected: real production instability isn't the concern (a
    corrected model id and explicit reasoning_effort=low fixed its earlier
    crashes), but it ran out of API credits mid-run and measured slower
    (avg 11.8s/call vs IndieRouter's 8.2s) - IndieRouter was also the
    provider every number above was actually validated against, so
    switching hosts now would be an untested variable on top of an
    already-large prompt/model change to the paid review path.

    Falls back to the existing Luna-or-DeepSeek path (writing_adapter_for's
    own default, via `fallback_model`) if INDIEROUTER_API_KEY isn't
    configured, so a deploy that hasn't rolled the new credential out yet
    degrades to the previous known-good behavior instead of hard-failing
    every Flash Review. `fallback_model` defaults to the same real
    DeepSeek model flash_review.FLASH_REVIEW_FALLBACK_MODEL names (passed
    explicitly by callers rather than imported directly, since flash_review
    imports FROM this module - importing back would be circular); passing
    GLM-5.3-Flash's own model id here would be wrong, since
    writing_adapter_for's fallback_model is only ever used to build a
    DeepSeek adapter, and "glm-5.3-flash" isn't a real DeepSeek model name.
    """
    if not _indierouter_available():
        logging.getLogger(__name__).warning(
            "INDIEROUTER_API_KEY not configured - falling back to Luna for Flash Review generation"
        )
        return writing_adapter_for(fallback_model, on_usage=on_usage, before_llm_call=before_llm_call)
    return OpenAICompatibleAdapter(
        name="IndieRouter",
        base_url="https://api.indierouter.ai/v1",
        api_key_env_var="INDIEROUTER_API_KEY",
        model=FLASH_REVIEW_GENERATION_MODEL,
        temperature=0.2,
        extra_body={"reasoning_effort": "low"},
        on_usage=on_usage,
        before_llm_call=before_llm_call,
    )


def flash_review_model_used(fallback_model: str) -> str:
    """The model name flash_review_generation_adapter will actually
    construct right now - mirrors resolve_model's own role (cost
    accounting/cache labeling must never drift from what actually ran).
    `fallback_model` matches resolve_model's own parameter: the model
    writing_adapter_for's Luna-or-DeepSeek fallback would use if
    INDIEROUTER_API_KEY isn't configured."""
    return FLASH_REVIEW_GENERATION_MODEL if _indierouter_available() else resolve_model(fallback_model)


# A real AIRview request once hung more than 15 minutes against IndieRouter
# at the default REQUEST_TIMEOUT_SECONDS (120s is too short to even trigger
# the hang-detection path on a legitimately large subsystem write) - this
# mitigation, paired with _call_with_retry's existing retry-on-failure, is
# what AIRview actually ships with. The hang's root cause was never
# reproduced; the timeout is the mitigation, not a fix.
AIRVIEW_REQUEST_TIMEOUT_SECONDS = 300

# Same mitigation, same reasoning, for managed audits: a real live smoke
# test against this repository (2026-10-04) hit a single .invoke() round
# with a 210,846-token prompt and a 14,971-token completion - at
# IndieRouter's own measured ~127 tokens/sec throughput, that's ~118s of
# generation alone, 2 seconds of margin under the 120s default before that
# one round would have timed out outright. Managed audits' rounds grow
# every turn (the whole accumulated conversation resends each time), so a
# later, even slightly larger round is a real, not hypothetical, risk -
# this is not AIRVIEW_REQUEST_TIMEOUT_SECONDS reused for a different
# reason, it is the identical problem (a legitimately large single-request
# generation needing more than 120s) on a second surface.
MANAGED_AUDIT_REQUEST_TIMEOUT_SECONDS = 300


def writing_adapter_for_airview(
    fallback_model: str,
    on_usage: Callable[[int, int, int], None] | None = None,
    before_llm_call: Callable[[], bool] | None = None,
    on_call_failed: Callable[[], None] | None = None,
    json_output: bool = False,
) -> OpenAICompatibleAdapter:
    """IndieRouter (deepseek-v4.1-flash) is AIRview's primary provider as of
    2026-10-04, never Luna regardless of OPENAI_API_KEY availability - see
    below for why Luna is excluded even as a fallback choice.

    Primary path settings (docs/operations/LLM-CONSOLIDATION-HANDOVER-
    2026-10-03.md, local-only): reasoning_effort=low (kept the slow arm's
    quality - +0.44/+0.59 vs RepoWise on two judges - while building in
    ~193s; reasoning off or default effort both scored lower at this
    concurrency/batch size) and AIRVIEW_REQUEST_TIMEOUT_SECONDS. Does NOT
    turn on json_mode even though real call sites pass json_output=True:
    AIRview has never actually reached a provider's real JSON mode (the
    DeepSeek-direct fallback below silently drops json_output on its own
    branch of writing_adapter_for - see that function's docstring), so
    this preserves existing real behavior rather than changing the
    provider and the JSON-mode behavior in the same change. Not a
    theoretical risk: this exact combination (deepseek-v4.1-flash via
    IndieRouter, effort low, no json_mode) is what the handover's own
    AIRview benchmark actually ran end to end - every subsystem/file-page
    description it reports parsing successfully was parsed with
    _parse_json_object's strict json.loads, no json_mode, no leniency.

    Falls back to the pre-existing, unchanged DeepSeek-direct path (never
    Luna - see below) if INDIEROUTER_API_KEY isn't configured.

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
    writing_adapter_for_plan. Managed audits also moved to IndieRouter
    separately - see writing_adapter_for_managed_audit below.
    """
    if _indierouter_available():
        return _indierouter_adapter(
            INDIEROUTER_DEEPSEEK_MODEL,
            on_usage=on_usage,
            before_llm_call=before_llm_call,
            on_call_failed=on_call_failed,
            reasoning_effort="low",
            json_mode=False,
            request_timeout_seconds=AIRVIEW_REQUEST_TIMEOUT_SECONDS,
        )
    logging.getLogger(__name__).warning(
        "INDIEROUTER_API_KEY not configured - falling back to direct DeepSeek for AIRview"
    )
    return writing_adapter_for(
        fallback_model,
        on_usage=on_usage,
        before_llm_call=before_llm_call,
        on_call_failed=on_call_failed,
        _prefer_luna=False,
        json_output=json_output,
    )


def airview_model_used(fallback_model: str) -> str:
    """The model writing_adapter_for_airview will actually construct right
    now - mirrors flash_review_model_used's own role (cost accounting and
    the evidence-packet cache's model_used label must never drift from
    what actually ran, or a provider switch silently corrupts cache hit/
    miss behavior)."""
    return INDIEROUTER_DEEPSEEK_MODEL if _indierouter_available() else fallback_model


# Not resolve_model(PRO_MODEL) or any other dynamic choice - always exactly
# this one model, unconditionally. See writing_adapter_for_managed_audit's
# docstring for the real numbers behind why.
MANAGED_AUDIT_MODEL = "deepseek-v4-flash"


def writing_adapter_for_managed_audit(
    on_usage: Callable[[int, int, int], None] | None = None,
    before_llm_call: Callable[[], bool] | None = None,
    on_call_failed: Callable[[], None] | None = None,
    allow_partial_report: bool = False,
) -> OpenAICompatibleAdapter:
    """IndieRouter (deepseek-v4.1-flash) is managed_audit's primary
    provider as of 2026-10-04 - never Luna, same reasoning as below.

    Primary path: reasoning_effort=low, paired with the hosted-only
    MANAGED_AUDIT_MAX_TOOL_ROUNDS=40 ceiling (openai_compatible.py - see
    its own comment for why this is explicit here rather than the plain
    adapter default) - measured directly against this repository via the
    real .invoke() tool-calling loop: 9 rounds, 132s, $0.16 list price,
    17/17 facts and fewer unverified citations (2) than default
    reasoning's 24-round, $0.67 run (5 unverified) - comfortably inside
    the ceiling either way (docs/operations/LLM-CONSOLIDATION-HANDOVER-
    2026-10-03.md, local-only). Re-verified live against this repository
    on 2026-10-04 (real IndieRouter call, not mocked): 5 rounds, 109.6s,
    $0.147, a full coherent report. Falls back to the pre-existing,
    unchanged DeepSeek-Flash-direct path (supports_tool_choice=False
    there - see writing_adapter_for's own comment on why; same
    MANAGED_AUDIT_MAX_TOOL_ROUNDS applies there too, since the hosted
    feature's cost reserve is already sized for it regardless of which
    provider actually answers) if INDIEROUTER_API_KEY isn't configured.

    Never Luna (writing_adapter_for_plan's default) and never DeepSeek Pro
    either, on either path. Measured directly, three real full audit runs
    against this repository, same evidence, same manual: Luna cost $0.15
    (6 rounds) and missed a real circular import; deepseek-v4-pro cost
    $1.15 (14 rounds) and caught it; deepseek-v4-flash cost $0.40 (16
    rounds) and also caught it. Pro's 3x-higher per-token rate over flash
    bought nothing here - pro actually used fewer total tokens than flash,
    so the extra cost was pure list-price premium, not more work done, for
    a shorter report and the identical finding. Flash is the only one of
    the three that is both accurate (matches Pro's finding) and cheap (a
    fraction of Pro's cost) for this specific task.

    This doesn't generalize from AIRview's own Luna-vs-DeepSeek finding
    above (or the other direction, Luna-preferred by default elsewhere):
    managed_audit is multi-round agentic tool use, not a single completion,
    and its cost is ~96% input-token-driven because every round re-sends
    the entire accumulated conversation - round-trip efficiency dominates
    over any model's per-token list price, which is exactly what made Pro
    the expensive choice here despite its higher-tier positioning.
    """
    if _indierouter_available():
        return _indierouter_adapter(
            INDIEROUTER_DEEPSEEK_MODEL,
            on_usage=on_usage,
            before_llm_call=before_llm_call,
            on_call_failed=on_call_failed,
            allow_partial_report=allow_partial_report,
            reasoning_effort="low",
            max_tool_rounds=MANAGED_AUDIT_MAX_TOOL_ROUNDS,
            request_timeout_seconds=MANAGED_AUDIT_REQUEST_TIMEOUT_SECONDS,
        )
    logging.getLogger(__name__).warning(
        "INDIEROUTER_API_KEY not configured - falling back to direct DeepSeek Flash for managed audits"
    )
    return writing_adapter_for(
        MANAGED_AUDIT_MODEL,
        on_usage=on_usage,
        before_llm_call=before_llm_call,
        on_call_failed=on_call_failed,
        allow_partial_report=allow_partial_report,
        _prefer_luna=False,
        max_tool_rounds=MANAGED_AUDIT_MAX_TOOL_ROUNDS,
        request_timeout_seconds=MANAGED_AUDIT_REQUEST_TIMEOUT_SECONDS,
    )


def managed_audit_model_used() -> str:
    """Mirrors airview_model_used - the model writing_adapter_for_managed_audit
    will actually construct right now, for _IncrementalSpendBudget's cost-
    accounting label."""
    return INDIEROUTER_DEEPSEEK_MODEL if _indierouter_available() else MANAGED_AUDIT_MODEL


def model_for_plan(plan: str) -> str:
    return resolve_model(PRO_MODEL)


def writing_adapter_for_plan(
    plan: str,
    on_usage: Callable[[int, int, int], None] | None = None,
    before_llm_call: Callable[[], bool] | None = None,
    on_call_failed: Callable[[], None] | None = None,
    allow_partial_report: bool = False,
    json_output: bool = False,
) -> OpenAICompatibleAdapter:
    return writing_adapter_for(
        PRO_MODEL,
        on_usage=on_usage,
        on_call_failed=on_call_failed,
        before_llm_call=before_llm_call,
        allow_partial_report=allow_partial_report,
        json_output=json_output,
    )


def writing_adapter_for_docs(
    fallback_model: str,
    on_usage: Callable[[int, int, int], None] | None = None,
    before_llm_call: Callable[[], bool] | None = None,
    on_call_failed: Callable[[], None] | None = None,
    allow_partial_report: bool = False,
    json_output: bool = True,
) -> OpenAICompatibleAdapter:
    """IndieRouter (deepseek-v4.1-flash) is Docs' primary provider as of
    2026-10-04, with JSON mode on - measured at 1.3% unsupported-claim
    rate, the best of five corpora judged (docs/operations/LLM-
    CONSOLIDATION-HANDOVER-2026-10-03.md, local-only). This is the first
    time Docs' json_output actually reaches a provider's real
    response_format=json_object end to end: PR #907 wired json_output
    through writing_adapter_for's OpenAI/Luna branch, but both real Docs
    call sites default to _prefer_luna=True and only land there when
    OPENAI_API_KEY happens to be configured - otherwise they silently drop
    json_output on the DeepSeek-direct branch (writing_adapter_for's own
    docstring). Default reasoning, not reasoning_effort=low - untested for
    Docs specifically, not carried over speculatively from AIRview/audits.
    Per-file parallelism is also untested and not introduced here; Docs
    stays strictly sequential per file/module.

    Falls back to the pre-existing writing_adapter_for(fallback_model,
    ...) path, unchanged, if INDIEROUTER_API_KEY isn't configured -
    `_prefer_luna` stays at its default True so each existing call site's
    own fallback behavior (Luna if OPENAI_API_KEY is configured, else
    DeepSeek direct) is preserved exactly.
    """
    if _indierouter_available():
        return _indierouter_adapter(
            INDIEROUTER_DEEPSEEK_MODEL,
            on_usage=on_usage,
            before_llm_call=before_llm_call,
            on_call_failed=on_call_failed,
            allow_partial_report=allow_partial_report,
            json_mode=json_output,
        )
    logging.getLogger(__name__).warning(
        "INDIEROUTER_API_KEY not configured - falling back to the previous Docs provider"
    )
    return writing_adapter_for(
        fallback_model,
        on_usage=on_usage,
        before_llm_call=before_llm_call,
        on_call_failed=on_call_failed,
        allow_partial_report=allow_partial_report,
        json_output=json_output,
    )


def docs_model_used(fallback_model: str) -> str:
    """Mirrors airview_model_used - fallback_model is whatever the caller
    would otherwise have resolved via resolve_model (PRO_MODEL for the
    full build, live_docs.FLASH_MODEL for incremental updates)."""
    return INDIEROUTER_DEEPSEEK_MODEL if _indierouter_available() else resolve_model(fallback_model)


# Same real model id as FLASH_REVIEW_GENERATION_MODEL - named separately so
# this surface's own choice (cheaper than deepseek-v4.1-flash, already
# proven in production for PR reviews, well suited to a short plain-text
# diagnosis) is legible on its own, not implied by reusing a Flash-Review-
# specific name.
HEALTH_FIX_SUGGESTION_MODEL = "glm-5.3-flash"


def writing_adapter_for_health_fix_suggestion(
    on_usage: Callable[[int, int, int], None] | None = None,
    on_call_failed: Callable[[], None] | None = None,
    fallback_model: str = PRO_MODEL,
) -> OpenAICompatibleAdapter:
    """IndieRouter (glm-5.3-flash) is the endpoint-health fix-suggestion's
    primary provider as of 2026-10-04 - genuinely new, no IndieRouter path
    existed for this surface before. reasoning_effort=low (GLM-5.3's
    thinking mode can't be disabled, only steered - see
    flash_review_generation_adapter's docstring for the measured reason
    "low" is the right steering value), plain text output, no JSON mode,
    no temperature override (unlike Flash Review's own 0.2, PR-Agent's
    convention for a different prompt shape - untested here, not carried
    over speculatively). Untested on IndieRouter end to end
    (docs/operations/LLM-CONSOLIDATION-HANDOVER-2026-10-03.md, local-only)
    - sanity-check manually before relying on it in production.

    Falls back to the pre-existing writing_adapter_for(fallback_model, ...)
    behavior, unchanged (Luna if OPENAI_API_KEY is configured, else
    DeepSeek Pro direct), if INDIEROUTER_API_KEY isn't configured."""
    if _indierouter_available():
        return _indierouter_adapter(
            HEALTH_FIX_SUGGESTION_MODEL,
            on_usage=on_usage,
            on_call_failed=on_call_failed,
            reasoning_effort="low",
        )
    logging.getLogger(__name__).warning(
        "INDIEROUTER_API_KEY not configured - falling back to the previous fix-suggestion provider"
    )
    return writing_adapter_for(fallback_model, on_usage=on_usage, on_call_failed=on_call_failed)


def health_fix_suggestion_model_used(plan: str) -> str:
    return HEALTH_FIX_SUGGESTION_MODEL if _indierouter_available() else model_for_plan(plan)


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

    # Groq's real published rate limit for openai/gpt-oss-120b is a tight
    # 8,000 tokens/minute - Flash Review's own per-file/aggregate context
    # caps (see github_api.MAX_CONTEXT_FILE_BYTES/MAX_CONTEXT_TOTAL_BYTES,
    # unconditional here too since fetch_review_file_context isn't
    # plan-gated) can already exceed that in a single real call regardless
    # of either cap's exact value - checked when MAX_CONTEXT_FILE_BYTES was
    # raised 80KB->100KB and confirmed not materially changed by that
    # raise, since Gemini (next in this chain) has enough headroom to
    # absorb what Groq rejects. Recorded here, not only in that PR's
    # description, so a future reader debugging a real Groq rejection
    # doesn't have to go dig up which PR mentioned it.
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
        # Captures the exact key before_llm_call reserved against, so
        # on_usage/on_call_failed correct that SAME day's counter even if
        # the real call straddles the UTC midnight boundary between
        # reservation and true-up - see both functions' own docstrings
        # for the real corruption this closes. threading.local(), not a
        # plain dict, so a concurrent second call through this same
        # adapter (this chain is built fresh per job today, but the
        # adapter object itself carries no such guarantee) gets its own
        # isolated slot instead of racing the first call's key through
        # one shared mutable cell - Flash Review finding: a plain dict
        # here means an overlapping second reservation overwrites the
        # first call's key, so a usage/failure callback can true-up or
        # release the WRONG call's Redis reservation.
        _reserved_key_local = threading.local()

        def _reserve_and_capture_key() -> bool:
            key = _openai_free_tier_token_key()
            ok = _reserve_openai_free_tier_budget(redis_conn, key=key)
            if ok:
                _reserved_key_local.key = key
            return ok

        def _true_up_reserved_key(real_total_tokens: int) -> None:
            key = getattr(_reserved_key_local, "key", None)
            if key is not None:
                del _reserved_key_local.key
            _true_up_openai_free_tier_reservation(redis_conn, real_total_tokens, key=key)

        def _on_openai_free_tier_usage(
            prompt_tokens: int, completion_tokens: int, cached_tokens: int = 0
        ) -> None:
            _true_up_reserved_key(prompt_tokens + completion_tokens)
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
            before_llm_call=_reserve_and_capture_key,
            # Releases the reservation before_llm_call just made when the
            # real call then fails (rate limit, auth error, timeout) -
            # on_usage never fires on a failed call, so without this the
            # reservation is permanently stuck against a call that used zero
            # real tokens. See openai_compatible.OpenAICompatibleAdapter's
            # on_call_failed for why this can't just reuse on_usage(0, 0):
            # that would misrepresent a failed call as a completed one to
            # any other on_usage consumer.
            on_call_failed=lambda: _true_up_reserved_key(0),
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
