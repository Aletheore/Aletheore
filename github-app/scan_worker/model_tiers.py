"""Which LLM the single Pro plan actually uses for one-time managed audit
reports, one-time AIRview builds, and the audit's LLM-based-suggestion
section - the pricing page's model claims are read from this file, not
written separately, so they can never drift from what actually runs.

PR reviews and AIRview's incremental updates don't go through this module
at all - they're hardcoded to deepseek-v4-flash directly in flash_review.py
and live_wiki.py, since they fire on every push/PR and need to stay cheap
regardless of which model the one-time work above uses.

Falls back to DeepSeek Pro if OPENAI_API_KEY isn't configured yet, so a
build never hard-fails on missing infra - logged, never silent, so this
is never mistaken for the intended path.
"""

import logging
from typing import Callable

from aletheore.adapters.openai_compatible import OpenAICompatibleAdapter
from aletheore.credentials import has_api_key

TERRA_MODEL = "gpt-5.6-terra"
PRO_FALLBACK_MODEL = "deepseek-v4-pro"


def model_for_plan(plan: str) -> str:
    """The model name writing_adapter_for_plan() will actually construct
    for this plan right now - used for cost accounting, so a spend cap
    never silently prices Pro's real tokens at DeepSeek's rate.
    """
    if plan != "pro":
        return PRO_FALLBACK_MODEL
    if has_api_key("OPENAI_API_KEY", "OpenAI"):
        return TERRA_MODEL
    logging.getLogger(__name__).warning(
        "OPENAI_API_KEY not configured - Pro's one-time audit/AIRview build falling back to DeepSeek"
    )
    return PRO_FALLBACK_MODEL


def writing_adapter_for_plan(
    plan: str, on_usage: Callable[[int, int], None] | None = None
) -> OpenAICompatibleAdapter:
    model = model_for_plan(plan)
    if model == TERRA_MODEL:
        return OpenAICompatibleAdapter(
            name="OpenAI",
            base_url="https://api.openai.com/v1",
            api_key_env_var="OPENAI_API_KEY",
            model=model,
            on_usage=on_usage,
        )
    return OpenAICompatibleAdapter(
        name="DeepSeek",
        base_url="https://api.deepseek.com",
        api_key_env_var="DEEPSEEK_API_KEY",
        model=model,
        # deepseek-v4-pro runs in thinking mode by default, which rejects
        # tool_choice="required" (400 invalid_request_error) - fall back to
        # the same unforced tool-choice path used for Ollama. Harmless for
        # callers that only use simple_completion(), which never sets this.
        supports_tool_choice=False,
        on_usage=on_usage,
    )
