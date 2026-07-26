from aletheore.adapters.openai_compatible import OpenAICompatibleAdapter
from scan_worker.model_tiers import (
    PRO_FALLBACK_MODEL,
    TERRA_MODEL,
    model_for_plan,
    writing_adapter_for_plan,
)


def _keys(monkeypatch, **available):
    monkeypatch.setattr(
        "scan_worker.model_tiers.has_api_key",
        lambda env_var, name, *a, **k: available.get(name, False),
    )


def test_pro_uses_terra_when_openai_key_present(monkeypatch):
    _keys(monkeypatch, OpenAI=True)
    adapter = writing_adapter_for_plan("pro")
    assert isinstance(adapter, OpenAICompatibleAdapter)
    assert adapter.name == "OpenAI"
    assert adapter._model == TERRA_MODEL


def test_pro_falls_back_to_deepseek_when_openai_key_missing(monkeypatch):
    _keys(monkeypatch)
    adapter = writing_adapter_for_plan("pro")
    assert adapter.name == "DeepSeek"
    assert adapter._model == PRO_FALLBACK_MODEL
    assert adapter._supports_tool_choice is False


def test_non_pro_plan_always_falls_back_to_deepseek(monkeypatch):
    # free (or any other non-"pro" value) should never reach Terra, even
    # with a real OpenAI key configured - this path shouldn't be reachable
    # in practice (managed audits already reject free plan earlier), but
    # the fallback must be safe regardless.
    _keys(monkeypatch, OpenAI=True)
    adapter = writing_adapter_for_plan("free")
    assert adapter.name == "DeepSeek"
    assert adapter._model == PRO_FALLBACK_MODEL


def test_on_usage_is_threaded_through_to_whichever_adapter_is_chosen(monkeypatch):
    _keys(monkeypatch)
    received = []
    adapter = writing_adapter_for_plan("pro", on_usage=lambda p, c: received.append((p, c)))
    adapter._on_usage(10, 20)
    assert received == [(10, 20)]


def test_model_for_plan_never_drifts_from_writing_adapter_for_plan(monkeypatch):
    # cost_for_usage() prices tokens by whatever model_for_plan() reports -
    # if it ever disagreed with the adapter writing_adapter_for_plan()
    # actually built, Pro's spend would be silently mispriced.
    for available in [{}, {"OpenAI": True}]:
        for plan in ["pro", "free"]:
            _keys(monkeypatch, **available)
            adapter = writing_adapter_for_plan(plan)
            assert model_for_plan(plan) == adapter._model, (plan, available)
