"""Which LLM each pricing tier actually uses to write managed audit
reports and one-time AIRview builds - the pricing page's model claims are
read from this file, not written separately, so they can never drift
from what actually runs.

Falls back one rung at a time toward DeepSeek if a higher tier's
provider key isn't configured yet, so a tier's builds never hard-fail on
missing infra - logged, never silent, so this is never mistaken for the
intended path.
"""

import logging
from typing import Callable

from aletheore.adapters.anthropic_native import AnthropicAdapter
from aletheore.adapters.openai_compatible import OpenAICompatibleAdapter
from aletheore.credentials import has_api_key

INDIE_MODEL = "deepseek-v4-pro"
TEAM_MODEL = "gpt-4o"
ENTERPRISE_MODEL = "claude-opus-4-8"


def model_for_plan(plan: str) -> str:
    """The model name writing_adapter_for_plan() will actually construct
    for this plan right now - used for cost accounting, so a spend cap
    never silently prices a tier's real tokens at DeepSeek's rate.
    """
    logger = logging.getLogger(__name__)
    if plan == "enterprise":
        if has_api_key("ANTHROPIC_API_KEY", "Anthropic"):
            return ENTERPRISE_MODEL
        logger.warning("ANTHROPIC_API_KEY not configured - enterprise tier falling back")
    if plan in ("enterprise", "team"):
        if has_api_key("OPENAI_API_KEY", "OpenAI"):
            return TEAM_MODEL
        logger.warning("OPENAI_API_KEY not configured - %s tier falling back to DeepSeek", plan)
    return INDIE_MODEL


def writing_adapter_for_plan(
    plan: str, on_usage: Callable[[int, int], None] | None = None
) -> OpenAICompatibleAdapter | AnthropicAdapter:
    model = model_for_plan(plan)
    if model == ENTERPRISE_MODEL:
        return AnthropicAdapter(model=model, on_usage=on_usage)
    if model == TEAM_MODEL:
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
