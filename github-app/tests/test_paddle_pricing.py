from app_server.paddle_pricing import resolve_plan_for_price_id


def test_resolves_all_six_real_price_ids():
    assert resolve_plan_for_price_id("pri_01ky9jwz35hvj5xs6f8xqw6htt") == "indie"
    assert resolve_plan_for_price_id("pri_01ky9jwzd6k9rhmnj8b4drbygg") == "indie"
    assert resolve_plan_for_price_id("pri_01ky9jx0gbx02mnn4d166yp3vc") == "team"
    assert resolve_plan_for_price_id("pri_01ky9jx0rkkkz75atfb29me9mn") == "team"
    assert resolve_plan_for_price_id("pri_01ky9jx1bkbbkfd9zspcgzd7p8") == "enterprise"
    assert resolve_plan_for_price_id("pri_01ky9jx1pbbpsexbmtbk1wfej1") == "enterprise"


def test_unknown_price_id_returns_none():
    assert resolve_plan_for_price_id("pri_totally_unknown") is None
