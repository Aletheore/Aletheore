from app_server.paddle_pricing import is_annual_subscription, resolve_plan_for_price_id, resolve_price_id_for_plan


def test_resolves_both_real_air_price_ids():
    assert resolve_plan_for_price_id("pri_01kyhevc8bkcghfpwjymz16y2h") == "air"
    assert resolve_plan_for_price_id("pri_01kyhevc9xn6z2nghmy8057jvp") == "air"


def test_unknown_price_id_returns_none():
    assert resolve_plan_for_price_id("pri_totally_unknown") is None


def test_resolve_price_id_for_plan_round_trips_both_intervals():
    monthly = resolve_price_id_for_plan("air", "month")
    annual = resolve_price_id_for_plan("air", "year")
    assert resolve_plan_for_price_id(monthly) == "air"
    assert resolve_plan_for_price_id(annual) == "air"
    assert monthly != annual


def test_is_annual_subscription_true_for_year_interval():
    assert is_annual_subscription({"billing_cycle": {"interval": "year"}}) is True


def test_is_annual_subscription_false_for_month_interval():
    assert is_annual_subscription({"billing_cycle": {"interval": "month"}}) is False


def test_is_annual_subscription_false_when_billing_cycle_missing():
    # Code-review finding: admin.py and dashboard.py each independently
    # hand-rolled (subscription.get("billing_cycle") or {}).get("interval")
    # == "year" - which is how the annual-credit display bug got
    # introduced twice. Shared here so there's one place to get it right.
    assert is_annual_subscription({}) is False
    assert is_annual_subscription({"billing_cycle": None}) is False


def test_resolve_price_id_for_plan_returns_none_for_unknown_plan_or_interval():
    assert resolve_price_id_for_plan("free", "month") is None
    assert resolve_price_id_for_plan("air", "week") is None


def test_flash_resolves_only_the_current_ten_dollar_price():
    assert resolve_price_id_for_plan("flash", "month") == "pri_01m3xpabbam5t2gkzwzmg0y9eq"
    assert resolve_plan_for_price_id("pri_01m3xpabbam5t2gkzwzmg0y9eq") == "flash"
    # The archived $8 price is never offered at checkout again.
    assert resolve_plan_for_price_id("pri_01m1dj0m1netz6ze1mmckz73nm") is None


def test_current_topup_price_is_accepted_alongside_the_legacy_one():
    from app_server.paddle_pricing import (
        ACCEPTED_CREDIT_TOPUP_PRICE_IDS,
        CREDIT_TOPUP_PRICE_ID,
        LEGACY_CREDIT_TOPUP_PRICE_IDS,
    )

    assert CREDIT_TOPUP_PRICE_ID not in LEGACY_CREDIT_TOPUP_PRICE_IDS
    assert ACCEPTED_CREDIT_TOPUP_PRICE_IDS == {CREDIT_TOPUP_PRICE_ID} | LEGACY_CREDIT_TOPUP_PRICE_IDS
