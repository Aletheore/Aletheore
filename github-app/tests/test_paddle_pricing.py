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


def test_the_top_up_form_enforces_the_same_minimum_as_the_paddle_price():
    # Paddle rejects a quantity below the price's quantity.minimum at checkout. If the form
    # (the input's min, the hint text, the click-time check) disagrees, a customer is told one
    # thing and stopped by another - this happened once with the check in buyCredit().
    from pathlib import Path

    from app_server.paddle_pricing import CREDIT_TOPUP_MIN_QUANTITY

    source = (Path(__file__).resolve().parent.parent / "app_server" / "frontend.py").read_text()
    n = CREDIT_TOPUP_MIN_QUANTITY

    assert f'id="topup-amount" min="{n}"' in source
    assert f"${n} minimum" in source
    assert f"amount < {n} || amount > 1000" in source
    assert f"between ${n} and $1000" in source
    # and no leftover copy of the old floor in the same form
    assert 'id="topup-amount" min="5"' not in source
    assert "between $5 and $1000" not in source
