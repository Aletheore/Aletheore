from aletheore.adapters.anthropic_native import AnthropicAdapter
from aletheore.adapters.openai_compatible import OpenAICompatibleAdapter
from scan_worker.model_tiers import (
    ENTERPRISE_MODEL,
    INDIE_MODEL,
    TEAM_MODEL,
    model_for_plan,
    writing_adapter_for_plan,
)


def _keys(monkeypatch, **available):
    monkeypatch.setattr(
        "scan_worker.model_tiers.has_api_key",
        lambda env_var, name, *a, **k: available.get(name, False),
    )


def test_indie_always_uses_deepseek_regardless_of_other_keys(monkeypatch):
    _keys(monkeypatch, OpenAI=True, Anthropic=True)
    adapter = writing_adapter_for_plan("indie")
    assert isinstance(adapter, OpenAICompatibleAdapter)
    assert adapter.name == "DeepSeek"
    assert adapter._model == INDIE_MODEL
    assert adapter._supports_tool_choice is False


def test_team_uses_gpt4o_when_openai_key_present(monkeypatch):
    _keys(monkeypatch, OpenAI=True)
    adapter = writing_adapter_for_plan("team")
    assert isinstance(adapter, OpenAICompatibleAdapter)
    assert adapter.name == "OpenAI"
    assert adapter._model == TEAM_MODEL


def test_team_falls_back_to_deepseek_when_openai_key_missing(monkeypatch):
    _keys(monkeypatch)
    adapter = writing_adapter_for_plan("team")
    assert adapter.name == "DeepSeek"
    assert adapter._model == INDIE_MODEL


def test_enterprise_uses_claude_opus_when_anthropic_key_present(monkeypatch):
    _keys(monkeypatch, Anthropic=True, OpenAI=True)
    adapter = writing_adapter_for_plan("enterprise")
    assert isinstance(adapter, AnthropicAdapter)
    assert adapter._model == ENTERPRISE_MODEL


def test_enterprise_falls_back_to_openai_when_only_openai_key_present(monkeypatch):
    _keys(monkeypatch, OpenAI=True)
    adapter = writing_adapter_for_plan("enterprise")
    assert isinstance(adapter, OpenAICompatibleAdapter)
    assert adapter.name == "OpenAI"
    assert adapter._model == TEAM_MODEL


def test_enterprise_falls_back_to_deepseek_when_no_keys_present(monkeypatch):
    _keys(monkeypatch)
    adapter = writing_adapter_for_plan("enterprise")
    assert adapter.name == "DeepSeek"
    assert adapter._model == INDIE_MODEL


def test_on_usage_is_threaded_through_to_whichever_adapter_is_chosen(monkeypatch):
    _keys(monkeypatch)
    received = []
    adapter = writing_adapter_for_plan("indie", on_usage=lambda p, c: received.append((p, c)))
    adapter._on_usage(10, 20)
    assert received == [(10, 20)]


def test_model_for_plan_never_drifts_from_writing_adapter_for_plan(monkeypatch):
    # cost_for_usage() prices tokens by whatever model_for_plan() reports -
    # if it ever disagreed with the adapter writing_adapter_for_plan()
    # actually built, a tier's spend would be silently mispriced.
    for available in [
        {},
        {"OpenAI": True},
        {"Anthropic": True},
        {"OpenAI": True, "Anthropic": True},
    ]:
        for plan in ["indie", "team", "enterprise"]:
            _keys(monkeypatch, **available)
            adapter = writing_adapter_for_plan(plan)
            assert model_for_plan(plan) == adapter._model, (plan, available)
