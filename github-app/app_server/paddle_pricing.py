"""Paddle price ID to Aletheore plan mapping."""

PADDLE_PRICE_TO_PLAN: dict[str, str] = {
    "pri_01ky9jwz35hvj5xs6f8xqw6htt": "indie",
    "pri_01ky9jwzd6k9rhmnj8b4drbygg": "indie",
    "pri_01ky9jx0gbx02mnn4d166yp3vc": "team",
    "pri_01ky9jx0rkkkz75atfb29me9mn": "team",
    "pri_01ky9jx1bkbbkfd9zspcgzd7p8": "enterprise",
    "pri_01ky9jx1pbbpsexbmtbk1wfej1": "enterprise",
}


def resolve_plan_for_price_id(price_id: str) -> str | None:
    return PADDLE_PRICE_TO_PLAN.get(price_id)


PLAN_INTERVAL_TO_PRICE_ID: dict[tuple[str, str], str] = {
    ("indie", "month"): "pri_01ky9jwz35hvj5xs6f8xqw6htt",
    ("indie", "year"): "pri_01ky9jwzd6k9rhmnj8b4drbygg",
    ("team", "month"): "pri_01ky9jx0gbx02mnn4d166yp3vc",
    ("team", "year"): "pri_01ky9jx0rkkkz75atfb29me9mn",
    ("enterprise", "month"): "pri_01ky9jx1bkbbkfd9zspcgzd7p8",
    ("enterprise", "year"): "pri_01ky9jx1pbbpsexbmtbk1wfej1",
}


def resolve_price_id_for_plan(plan: str, interval: str) -> str | None:
    return PLAN_INTERVAL_TO_PRICE_ID.get((plan, interval))
