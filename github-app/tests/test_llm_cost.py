from datetime import date

import pytest

from app_server import llm_cost
from app_server.llm_cost import (
    WARN_FRACTION_OF_CAP,
    base_cap_for_plan,
    base_credit_for_plan,
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


def test_cost_for_usage_deepseek_v4_1_flash():
    # IndieRouter's own model id (AIRview/Docs/managed-audits primary
    # provider as of 2026-10-04) - a different, correctly separate entry
    # from "deepseek-v4-flash" above, which prices the direct DeepSeek API.
    assert cost_for_usage("deepseek-v4.1-flash", 1_000_000, 1_000_000) == pytest.approx(
        0.2273 + 0.6819
    )


def test_cost_for_usage_gpt_5_6_luna():
    assert cost_for_usage("gpt-5.6-luna", 1_000_000, 1_000_000) == pytest.approx(
        0.20 + 1.20
    )


def test_cost_for_usage_small_real_call():
    expected = (2_000 * 0.44 + 300 * 1.32) / 1_000_000
    assert cost_for_usage("deepseek-v4-flash", 2_000, 300) == pytest.approx(expected)


def test_base_cap_for_plan_air_uses_its_own_explicit_override_not_the_shared_fraction():
    # Real, deliberate: $20, not $29.99 * CAP_FRACTION_OF_PRICE (0.5) = $14.995 -
    # raised to give large real repos (AIRview/Docs full-build coverage
    # scaling to repo size) enough real headroom to reach full coverage
    # without needing many more 48h catch-up cycles than a small repo would.
    assert base_cap_for_plan("air") == 20.00


def test_base_cap_for_plan_free_or_unknown_plan_has_no_budget():
    assert base_cap_for_plan("free") == 0.0
    assert base_cap_for_plan("not-a-real-plan") == 0.0


def test_base_cap_for_plan_flash_uses_its_own_explicit_override_not_the_shared_fraction():
    # Real, deliberate: $6, not $8 * CAP_FRACTION_OF_PRICE (0.5) = $4 -
    # flash's real worst-case cost data justified a looser fraction than
    # the shared default, so it's a real override, not derived from price.
    # Raised from $5 alongside MAX_CONTEXT_FILE_BYTES's 80KB->100KB raise -
    # see PLAN_CAP_OVERRIDE_USD's own comment for the real cost math.
    assert base_cap_for_plan("flash") == 6.00


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


def test_cost_for_usage_unknown_model_does_not_crash_and_is_not_priced_at_the_most_expensive_model(monkeypatch, caplog):
    """Code-review finding: a bare dict lookup used to KeyError straight out
    of a usage-accounting callback the moment a model got added to
    model_tiers.py without a matching MODEL_RATES_PER_MILLION_USD entry. An
    unknown model must degrade instead of crashing - but not to the table's
    HIGHEST rate: that is claude-opus-4-8 at $15/$75 per M, about 200x the
    production generator, and this number is drawn from the customer's real
    credit balance, so it would silently overcharge them. It is priced at the
    table's median, and logged at ERROR so it reaches Sentry."""
    import statistics

    monkeypatch.setattr(llm_cost, "_warned_stale_models", set())
    rates = llm_cost.MODEL_RATES_PER_MILLION_USD.values()
    median_input = statistics.median(r["input"] for r in rates)
    median_output = statistics.median(r["output"] for r in rates)
    highest_input = max(r["input"] for r in rates)
    highest_output = max(r["output"] for r in rates)

    with caplog.at_level("WARNING"):
        cost = cost_for_usage("some-brand-new-model-nobody-priced-yet", 1_000_000, 1_000_000)

    assert cost == pytest.approx(median_input + median_output)
    assert cost < (highest_input + highest_output) / 10
    errors = [r for r in caplog.records if r.levelname == "ERROR" and "some-brand-new-model-nobody-priced-yet" in r.message]
    assert len(errors) == 1


def test_cost_for_usage_unknown_model_with_an_empty_rate_table_prices_at_zero_instead_of_crashing(monkeypatch):
    monkeypatch.setattr(llm_cost, "_warned_stale_models", set())
    monkeypatch.setattr(llm_cost, "MODEL_RATES_PER_MILLION_USD", {})

    assert cost_for_usage("any-model", 1_000_000, 1_000_000) == 0.0


def test_cost_for_usage_unknown_model_warns_only_once(monkeypatch, caplog):
    monkeypatch.setattr(llm_cost, "_warned_stale_models", set())

    with caplog.at_level("WARNING"):
        cost_for_usage("another-unpriced-model", 1000, 1000)
        cost_for_usage("another-unpriced-model", 1000, 1000)

    warnings = [r for r in caplog.records if "another-unpriced-model" in r.message]
    assert len(warnings) == 1


def test_stale_models_tolerates_a_malformed_verified_at_instead_of_crashing(monkeypatch):
    """Code-review finding: date.fromisoformat used to raise uncaught on a
    malformed verified_at on ANY entry - even one never actually billed -
    which crashed cost accounting for every model, not just the broken
    one. A malformed entry is now treated as stale (the conservative
    direction) instead."""
    monkeypatch.setitem(
        llm_cost.MODEL_RATES_PER_MILLION_USD,
        "some-model-with-a-bad-date",
        {"input": 1.0, "output": 1.0, "verified_at": "not-a-real-date"},
    )

    assert "some-model-with-a-bad-date" in stale_models(as_of=date(2026, 7, 23))


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


def test_base_credit_for_plan_flash():
    assert base_credit_for_plan("flash", extra_seats=0) == 5.00


def test_base_credit_for_plan_air_no_extra_seats():
    assert base_credit_for_plan("air", extra_seats=0) == 18.00


def test_base_credit_for_plan_air_with_extra_seats():
    # Same per-seat bonus the old monthly_cap_for_installation used -
    # EXTRA_SEAT_LLM_CAP_USD ($3.00) per extra seat.
    assert base_credit_for_plan("air", extra_seats=2) == 18.00 + 2 * 3.00


def test_base_credit_for_plan_unknown_plan_is_zero():
    assert base_credit_for_plan("free", extra_seats=0) == 0.0


def test_base_credit_for_plan_air_annual_gets_ten_months_of_credit():
    # The annual price is 2 months free ($299.90 vs 12 x $29.99); the
    # included credit follows the same discount: $15/month = $180/year.
    assert base_credit_for_plan("air", extra_seats=0, is_annual=True) == 15.00
    assert base_credit_for_plan("air", extra_seats=0, is_annual=False) == 18.00


def test_base_credit_for_plan_air_annual_keeps_the_per_seat_bonus():
    assert base_credit_for_plan("air", extra_seats=2, is_annual=True) == 15.00 + 2 * 3.00


def test_base_credit_for_plan_ignores_annual_for_a_plan_with_no_annual_price():
    assert base_credit_for_plan("flash", extra_seats=0, is_annual=True) == 5.00
