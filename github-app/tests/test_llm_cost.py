from datetime import date

import pytest

from app_server import llm_cost
from app_server.llm_cost import (
    WARN_FRACTION_OF_CAP,
    base_cap_for_plan,
    cost_for_usage,
    crossed_spend_warning_threshold,
    monthly_cap_for_installation,
    stale_models,
)


def test_cost_for_usage_deepseek_v4_pro():
    assert cost_for_usage("deepseek-v4-pro", 1_000_000, 1_000_000) == pytest.approx(
        1.32 + 3.96
    )


def test_cost_for_usage_deepseek_v4_flash():
    assert cost_for_usage("deepseek-v4-flash", 1_000_000, 1_000_000) == pytest.approx(
        0.44 + 1.32
    )


def test_cost_for_usage_gpt_5_6_luna():
    assert cost_for_usage("gpt-5.6-luna", 1_000_000, 1_000_000) == pytest.approx(
        0.20 + 1.20
    )


def test_cost_for_usage_small_real_call():
    expected = (2_000 * 0.44 + 300 * 1.32) / 1_000_000
    assert cost_for_usage("deepseek-v4-flash", 2_000, 300) == pytest.approx(expected)


def test_base_cap_for_plan_is_half_of_tier_price():
    assert base_cap_for_plan("air") == pytest.approx(14.995)


def test_base_cap_for_plan_free_or_unknown_plan_has_no_budget():
    assert base_cap_for_plan("free") == 0.0
    assert base_cap_for_plan("not-a-real-plan") == 0.0


def test_monthly_cap_for_installation_base_only():
    assert monthly_cap_for_installation(7.00, 0) == pytest.approx(7.00)


def test_monthly_cap_for_installation_with_extra_seats():
    assert monthly_cap_for_installation(7.00, 3) == pytest.approx(16.00)


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


def test_crossed_spend_warning_threshold_fires_on_the_crossing_increment():
    """A $10 increment against a $15 cap crosses the 30% ($4.50) threshold
    partway through - previous total ($2) was under it, new total ($12) is
    over."""
    assert crossed_spend_warning_threshold(2.0, 12.0, 15.0) is True


def test_crossed_spend_warning_threshold_does_not_fire_while_still_under():
    assert crossed_spend_warning_threshold(1.0, 2.0, 15.0) is False


def test_crossed_spend_warning_threshold_does_not_refire_once_already_over():
    """Edge-triggered: an installation already past the threshold must not
    log again on every subsequent call for the rest of the month."""
    assert crossed_spend_warning_threshold(10.0, 11.0, 15.0) is False


def test_crossed_spend_warning_threshold_fires_exactly_at_the_boundary():
    threshold = WARN_FRACTION_OF_CAP * 15.0
    assert crossed_spend_warning_threshold(threshold - 0.01, threshold, 15.0) is True


def test_crossed_spend_warning_threshold_never_fires_for_a_zero_cap():
    """A zero cap means no plan matched (base_cap_for_plan's default) - not
    a real installation to warn about."""
    assert crossed_spend_warning_threshold(0.0, 100.0, 0.0) is False
