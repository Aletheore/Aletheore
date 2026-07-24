from datetime import date

import pytest

from app_server import llm_cost
from app_server.llm_cost import base_cap_for_plan, cost_for_usage, monthly_cap_for_installation, stale_models


def test_cost_for_usage_deepseek_v4_pro():
    assert cost_for_usage("deepseek-v4-pro", 1_000_000, 1_000_000) == pytest.approx(
        0.435 + 0.87
    )


def test_cost_for_usage_deepseek_v4_flash():
    assert cost_for_usage("deepseek-v4-flash", 1_000_000, 1_000_000) == pytest.approx(
        0.14 + 0.28
    )


def test_cost_for_usage_small_real_call():
    expected = (2_000 * 0.14 + 300 * 0.28) / 1_000_000
    assert cost_for_usage("deepseek-v4-flash", 2_000, 300) == pytest.approx(expected)


def test_base_cap_for_plan_is_half_of_tier_price():
    assert base_cap_for_plan("indie") == pytest.approx(14.50)
    assert base_cap_for_plan("team") == pytest.approx(39.50)
    assert base_cap_for_plan("enterprise") == pytest.approx(99.50)


def test_base_cap_for_plan_free_or_unknown_plan_has_no_budget():
    assert base_cap_for_plan("free") == 0.0
    assert base_cap_for_plan("not-a-real-plan") == 0.0


def test_monthly_cap_for_installation_base_only():
    assert monthly_cap_for_installation(7.00, 0) == pytest.approx(7.00)


def test_monthly_cap_for_installation_with_extra_seats():
    assert monthly_cap_for_installation(7.00, 3) == pytest.approx(13.00)


def test_stale_models_returns_empty_when_all_recently_verified():
    assert stale_models(as_of=date(2026, 7, 24)) == []


def test_stale_models_flags_a_model_past_max_age(monkeypatch):
    monkeypatch.setitem(
        llm_cost.MODEL_RATES_PER_MILLION_USD,
        "deepseek-v4-pro",
        {"input": 0.435, "output": 0.87, "verified_at": "2026-01-01"},
    )

    assert stale_models(as_of=date(2026, 7, 23)) == ["deepseek-v4-pro"]


def test_stale_models_respects_custom_max_age(monkeypatch):
    monkeypatch.setitem(
        llm_cost.MODEL_RATES_PER_MILLION_USD,
        "deepseek-v4-pro",
        {"input": 0.435, "output": 0.87, "verified_at": "2026-07-01"},
    )

    assert stale_models(as_of=date(2026, 7, 23), max_age_days=90) == []
    assert stale_models(as_of=date(2026, 7, 23), max_age_days=10) == ["deepseek-v4-pro"]


def test_cost_for_usage_warns_once_per_model_for_stale_pricing(monkeypatch, caplog):
    monkeypatch.setitem(
        llm_cost.MODEL_RATES_PER_MILLION_USD,
        "deepseek-v4-pro",
        {"input": 0.435, "output": 0.87, "verified_at": "2020-01-01"},
    )
    monkeypatch.setattr(llm_cost, "_warned_stale_models", set())

    with caplog.at_level("WARNING"):
        cost_for_usage("deepseek-v4-pro", 1000, 1000)
        cost_for_usage("deepseek-v4-pro", 1000, 1000)

    stale_warnings = [r for r in caplog.records if "deepseek-v4-pro" in r.message]
    assert len(stale_warnings) == 1


def test_cost_for_usage_does_not_warn_for_freshly_verified_model(monkeypatch, caplog):
    monkeypatch.setattr(llm_cost, "_warned_stale_models", set())

    with caplog.at_level("WARNING"):
        cost_for_usage("deepseek-v4-flash", 1000, 1000)

    assert caplog.records == []
