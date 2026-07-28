"""Paddle price ID to Aletheore plan mapping."""

# The AIR add-on for a seat beyond the plan's included count ($3.99/mo,
# pricing.html's "+$3.99/mo per additional team member"). Added as a second
# line item (by quantity) on a customer's existing AIR subscription, not a
# separate subscription of its own.
EXTRA_SEAT_PRICE_ID = "pri_01kym2q99kevmdg7h71nwpm4ej"

PADDLE_PRICE_TO_PLAN: dict[str, str] = {
    "pri_01kyhevc8bkcghfpwjymz16y2h": "air",
    "pri_01kyhevc9xn6z2nghmy8057jvp": "air",
}


def resolve_plan_for_price_id(price_id: str) -> str | None:
    return PADDLE_PRICE_TO_PLAN.get(price_id)


PLAN_INTERVAL_TO_PRICE_ID: dict[tuple[str, str], str] = {
    ("air", "month"): "pri_01kyhevc8bkcghfpwjymz16y2h",
    ("air", "year"): "pri_01kyhevc9xn6z2nghmy8057jvp",
}


def resolve_price_id_for_plan(plan: str, interval: str) -> str | None:
    return PLAN_INTERVAL_TO_PRICE_ID.get((plan, interval))
