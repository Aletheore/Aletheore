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
from typing import Callable

from aletheore.adapters.openai_compatible import OpenAICompatibleAdapter
from aletheore.credentials import has_api_key

LUNA_MODEL = "gpt-5.6-luna"
PRO_MODEL = "deepseek-v4-pro"

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
    on_usage: Callable[[int, int], None] | None = None,
    before_llm_call: Callable[[], bool] | None = None,
    allow_partial_report: bool = False,
) -> OpenAICompatibleAdapter:
    if _openai_available():
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


def model_for_plan(plan: str) -> str:
    return resolve_model(PRO_MODEL)


def writing_adapter_for_plan(
    plan: str,
    on_usage: Callable[[int, int], None] | None = None,
    before_llm_call: Callable[[], bool] | None = None,
    allow_partial_report: bool = False,
) -> OpenAICompatibleAdapter:
    return writing_adapter_for(
        PRO_MODEL,
        on_usage=on_usage,
        before_llm_call=before_llm_call,
        allow_partial_report=allow_partial_report,
    )
