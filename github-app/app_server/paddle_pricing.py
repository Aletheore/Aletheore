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
