"""Paddle price ID to Aletheore plan mapping."""

# The AIR add-on for a seat beyond the plan's included count ($6.99/mo,
# pricing.html's "+$6.99/mo per additional team member"). Added as a second
# line item (by quantity) on a customer's existing AIR subscription, not a
# separate subscription of its own. Replaces the $4.99 price
# (pri_01kzks8ccwf6h5bxxtmjfdy1fg, now archived in Paddle), which itself
# replaced the original $3.99 price (pri_01kym2q99kevmdg7h71nwpm4ej, also
# archived) - each swap archived the old price rather than mutating it in
# place, and each was only done after confirming zero live subscribers on
# it, so no pre-existing subscriber was ever silently repriced. The included
# seat count for the "air" plan also dropped from 5 to 3 alongside this
# price change (db.py's INCLUDED_SEATS) - 5 seats was too much value to
# bundle into the base $29.99/mo price.
EXTRA_SEAT_PRICE_ID = "pri_01m123rwvvtgbm6bmmxcbav4hh"

# The flash plan's real Paddle product (pro_01m1754jf8nkvhrn3sbaj9rmyq,
# "Aletheore Flash") and its one price - monthly only, no annual yet,
# matching what was actually validated (real cost/recall data checked
# against an 800-review cap - see llm_cost.py's PLAN_CAP_OVERRIDE_USD).
# Created live via the Paddle MCP, not a placeholder - real, chargeable
# as of 2026-08-29.
#
# $6/mo (pri_01m1754jr5msg62grry49kjhw5) replaced by $8/mo
# (pri_01m1dj0m1netz6ze1mmckz73nm) on 2026-09-01: the $6 price left thin
# worst-case margin once Paddle's cut was factored in against the
# measured ~$3.47 worst-case LLM cost for 800 reviews/month. Swapped
# rather than mutated in place (same pattern as EXTRA_SEAT_PRICE_ID's
# history above), only after confirming zero live subscribers on the old
# price via the Paddle MCP - it had only been live a few hours (PR #492)
# and never had a real subscriber, so there was nothing to migrate.
PADDLE_PRICE_TO_PLAN: dict[str, str] = {
    "pri_01kyhevc8bkcghfpwjymz16y2h": "air",
    "pri_01kyhevc9xn6z2nghmy8057jvp": "air",
    "pri_01m1dj0m1netz6ze1mmckz73nm": "flash",
}


def resolve_plan_for_price_id(price_id: str) -> str | None:
    return PADDLE_PRICE_TO_PLAN.get(price_id)


PLAN_INTERVAL_TO_PRICE_ID: dict[tuple[str, str], str] = {
    ("air", "month"): "pri_01kyhevc8bkcghfpwjymz16y2h",
    ("air", "year"): "pri_01kyhevc9xn6z2nghmy8057jvp",
    ("flash", "month"): "pri_01m1dj0m1netz6ze1mmckz73nm",
}


def resolve_price_id_for_plan(plan: str, interval: str) -> str | None:
    return PLAN_INTERVAL_TO_PRICE_ID.get((plan, interval))
