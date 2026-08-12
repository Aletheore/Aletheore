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
from typing import Callable

from aletheore.adapters.openai_compatible import OpenAICompatibleAdapter
from aletheore.credentials import has_api_key

LUNA_MODEL = "gpt-5.6-luna"
PRO_MODEL = "deepseek-v4-pro"


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
