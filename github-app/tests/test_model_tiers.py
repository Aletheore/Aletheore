import logging

from aletheore.adapters.openai_compatible import OpenAICompatibleAdapter
from scan_worker.model_tiers import (
    LUNA_MODEL,
    PRO_MODEL,
    model_for_plan,
    resolve_model,
    writing_adapter_for,
    writing_adapter_for_plan,
)


def test_resolve_model_returns_luna_when_openai_key_configured(monkeypatch):
    monkeypatch.setattr("scan_worker.model_tiers.has_api_key", lambda *a, **k: True)
    assert resolve_model("some-fallback") == LUNA_MODEL


def test_resolve_model_falls_back_when_openai_key_not_configured(monkeypatch):
    monkeypatch.setattr("scan_worker.model_tiers.has_api_key", lambda *a, **k: False)
    assert resolve_model("some-fallback") == "some-fallback"


def test_writing_adapter_for_builds_openai_adapter_when_key_configured(monkeypatch):
    monkeypatch.setattr("scan_worker.model_tiers.has_api_key", lambda *a, **k: True)
    adapter = writing_adapter_for("some-fallback")
    assert isinstance(adapter, OpenAICompatibleAdapter)
    assert adapter.name == "OpenAI"
    assert adapter._model == LUNA_MODEL
    assert adapter._base_url == "https://api.openai.com/v1"
    assert adapter._api_key_env_var == "OPENAI_API_KEY"


def test_writing_adapter_for_falls_back_to_deepseek_when_key_not_configured(monkeypatch, caplog):
    monkeypatch.setattr("scan_worker.model_tiers.has_api_key", lambda *a, **k: False)
    with caplog.at_level(logging.WARNING, logger="scan_worker.model_tiers"):
        adapter = writing_adapter_for("deepseek-v4-flash")
    assert adapter.name == "DeepSeek"
    assert adapter._model == "deepseek-v4-flash"
    assert adapter._supports_tool_choice is False
    assert "OPENAI_API_KEY not configured" in caplog.text


def test_writing_adapter_for_threads_on_usage_through_either_branch(monkeypatch):
    for key_configured in (True, False):
        monkeypatch.setattr("scan_worker.model_tiers.has_api_key", lambda *a, **k: key_configured)
        received = []
        adapter = writing_adapter_for("deepseek-v4-flash", on_usage=lambda p, c: received.append((p, c)))
        adapter._on_usage(10, 20)
        assert received == [(10, 20)], key_configured


def test_pro_uses_luna_when_openai_key_configured(monkeypatch):
    monkeypatch.setattr("scan_worker.model_tiers.has_api_key", lambda *a, **k: True)
    adapter = writing_adapter_for_plan("pro")
    assert adapter.name == "OpenAI"
    assert adapter._model == LUNA_MODEL


def test_pro_falls_back_to_deepseek_pro_when_openai_key_not_configured(monkeypatch):
    monkeypatch.setattr("scan_worker.model_tiers.has_api_key", lambda *a, **k: False)
    adapter = writing_adapter_for_plan("pro")
    assert isinstance(adapter, OpenAICompatibleAdapter)
    assert adapter.name == "DeepSeek"
    assert adapter._model == PRO_MODEL
    assert adapter._supports_tool_choice is False


def test_non_pro_plan_resolves_the_same_as_pro(monkeypatch):
    # free (or any other non-"pro" value) resolves identically - there is
    # only one plan's worth of routing left, this path shouldn't be
    # reachable in practice (managed audits already reject free plan
    # earlier), but the behavior must be safe regardless.
    for key_configured in (True, False):
        monkeypatch.setattr("scan_worker.model_tiers.has_api_key", lambda *a, **k: key_configured)
        assert writing_adapter_for_plan("free")._model == writing_adapter_for_plan("pro")._model


def test_model_for_plan_never_drifts_from_writing_adapter_for_plan(monkeypatch):
    # cost_for_usage() prices tokens by whatever model_for_plan() reports -
    # if it ever disagreed with the adapter writing_adapter_for_plan()
    # actually built, Pro's spend would be silently mispriced.
    for key_configured in (True, False):
        monkeypatch.setattr("scan_worker.model_tiers.has_api_key", lambda *a, **k: key_configured)
        for plan in ["pro", "free"]:
            adapter = writing_adapter_for_plan(plan)
            assert model_for_plan(plan) == adapter._model, (key_configured, plan)
