from aletheore.adapters.openai_compatible import OpenAICompatibleAdapter
from scan_worker.model_tiers import PRO_MODEL, model_for_plan, writing_adapter_for_plan


def test_pro_uses_deepseek_pro():
    adapter = writing_adapter_for_plan("pro")
    assert isinstance(adapter, OpenAICompatibleAdapter)
    assert adapter.name == "DeepSeek"
    assert adapter._model == PRO_MODEL
    assert adapter._supports_tool_choice is False


def test_non_pro_plan_also_uses_deepseek_pro():
    # free (or any other non-"pro" value) still gets DeepSeek Pro - this
    # path shouldn't be reachable in practice (managed audits already
    # reject free plan earlier), but the behavior must be safe regardless.
    adapter = writing_adapter_for_plan("free")
    assert adapter.name == "DeepSeek"
    assert adapter._model == PRO_MODEL


def test_on_usage_is_threaded_through_to_the_adapter():
    received = []
    adapter = writing_adapter_for_plan("pro", on_usage=lambda p, c: received.append((p, c)))
    adapter._on_usage(10, 20)
    assert received == [(10, 20)]


def test_model_for_plan_never_drifts_from_writing_adapter_for_plan():
    # cost_for_usage() prices tokens by whatever model_for_plan() reports -
    # if it ever disagreed with the adapter writing_adapter_for_plan()
    # actually built, Pro's spend would be silently mispriced.
    for plan in ["pro", "free"]:
        adapter = writing_adapter_for_plan(plan)
        assert model_for_plan(plan) == adapter._model, plan
