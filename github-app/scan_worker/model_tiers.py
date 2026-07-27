"""Which LLM the single Pro plan actually uses for one-time managed audit
reports and one-time AIRview builds - the pricing page's model claims are
read from this file, not written separately, so they can never drift from
what actually runs.

PR reviews and AIRview's incremental updates don't go through this module
at all - they're hardcoded to deepseek-v4-flash directly in flash_review.py
and live_wiki.py, since they fire on every push/PR and need to stay cheap
regardless of which model the one-time work above uses.

Always DeepSeek Pro - a single, uniform model for every Pro-plan LLM call,
matching jobs.py's own health fix-suggestion adapter, which already made
this same call ("never tier-routed... a tier that no longer exists").
"""

from typing import Callable

from aletheore.adapters.openai_compatible import OpenAICompatibleAdapter

PRO_MODEL = "deepseek-v4-pro"


def model_for_plan(plan: str) -> str:
    """The model name writing_adapter_for_plan() will actually construct
    for this plan right now - used for cost accounting, so a spend cap
    never silently prices Pro's real tokens at the wrong rate.
    """
    return PRO_MODEL


def writing_adapter_for_plan(
    plan: str, on_usage: Callable[[int, int], None] | None = None
) -> OpenAICompatibleAdapter:
    return OpenAICompatibleAdapter(
        name="DeepSeek",
        base_url="https://api.deepseek.com",
        api_key_env_var="DEEPSEEK_API_KEY",
        model=model_for_plan(plan),
        # deepseek-v4-pro runs in thinking mode by default, which rejects
        # tool_choice="required" (400 invalid_request_error) - fall back to
        # the same unforced tool-choice path used for Ollama. Harmless for
        # callers that only use simple_completion(), which never sets this.
        supports_tool_choice=False,
        on_usage=on_usage,
    )
