from app_server.paddle_pricing import resolve_plan_for_price_id, resolve_price_id_for_plan


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


def test_resolve_price_id_for_plan_returns_none_for_unknown_plan_or_interval():
    assert resolve_price_id_for_plan("free", "month") is None
    assert resolve_price_id_for_plan("air", "week") is None
