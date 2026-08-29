import logging
from datetime import date

logger = logging.getLogger(__name__)

# Cache-miss, list-price rates only - provider list prices, confirm still
# current before relying on them for real spend accounting. Overestimating
# cost is the safe direction for a hard cap, so when in doubt round up.
# verified_at is the date these numbers were last checked against the
# provider's own pricing page - not a promise the price hasn't moved
# since, just an honest record of how stale it might be.
MODEL_RATES_PER_MILLION_USD = {
    # Peak-hour, cache-miss rate - DeepSeek repriced 2026-08-16 (confirmed
    # against api-docs.deepseek.com/quick_start/pricing) and now publishes
    # four real rates per model: peak/off-peak x cache-hit/cache-miss. Peak
    # cache-miss is the worst case of the four, matching this table's own
    # "overestimate is the safe direction" rule above - a real DeepSeek call
    # is very often billed at the much cheaper cache-hit rate (managed_audit
    # measured 96% cache hit on its shared, append-only conversation prefix
    # in a real test), so this constant is deliberately a ceiling, not an
    # estimate of typical real cost.
    "deepseek-v4-pro": {"input": 1.32, "output": 3.96, "verified_at": "2026-08-29"},
    "deepseek-v4-flash": {"input": 0.44, "output": 1.32, "verified_at": "2026-08-29"},
    "gpt-5.6-luna": {"input": 0.20, "output": 1.20, "verified_at": "2026-08-09"},
    # Embeddings bill on input only, so output is 0 rather than absent -
    # cost_for_usage multiplies both, and a missing key would KeyError
    # rather than cost nothing.
    "text-embedding-3-small": {"input": 0.02, "output": 0.0, "verified_at": "2026-08-11"},
    "gpt-4o": {"input": 2.50, "output": 10.00, "verified_at": "2026-07-23"},
    "claude-opus-4-8": {"input": 15.00, "output": 75.00, "verified_at": "2026-07-23"},
}

STALE_PRICE_MAX_AGE_DAYS = 90

# What a seat actually bills at (paddle_pricing.EXTRA_SEAT_PRICE_ID,
# pricing.html's "+$6.99/mo per additional team member").
EXTRA_SEAT_PRICE_USD = 6.99

# How much of that a seat is allowed to add to the LLM spend cap. Kept
# deliberately below EXTRA_SEAT_PRICE_USD (not equal to it, as this used to
# be) so a seat has guaranteed positive worst-case margin instead of being
# a wash - at cap, a seat used to cost exactly what it earned.
EXTRA_SEAT_LLM_CAP_USD = 3.00

# Base monthly price per plan (github-app/../website/pricing.html) - the hard
# LLM spend cap is set as a fraction of this, not a flat dollar figure, so it
# scales with what the tier actually pays rather than under- or over-capping
# it. Single paid tier (Aletheore AIR) - priced monthly regardless of
# whether a given customer actually pays monthly or annually, since the
# spend cap is a monthly rolling figure either way.
PLAN_MONTHLY_PRICE_USD = {
    "air": 29.99,
    "flash": 6.00,
}

# flash's real spend cap is a deliberately looser fraction of its price
# than the shared 50% default below (~67%, $4 of $6) - real worst-case
# cost for 1000 reviews of solo Luna generation (no dual-agent
# verification, compact + trimmed diff) measured at ~$4.34, small enough
# in absolute dollars that the tighter formula wasn't worth applying
# here. See jobs.py's MAX_FLASH_TIER_FLASH_REVIEWS_PER_MONTH for the real
# review-count cap (800) this was checked against.
PLAN_CAP_OVERRIDE_USD = {
    "flash": 4.00,
}

# Deliberately generous: this is a worst-case abuse/runaway-cost ceiling, not
# a target for typical spend. Normal usage is expected to land far below it -
# if real usage routinely approaches this fraction, that's a signal to
# investigate (a caching regression, a runaway loop), not to raise the cap.
CAP_FRACTION_OF_PRICE = 0.5

# Warn once per process per model, not once per call - cost_for_usage()
# runs on every token-usage callback, and a real deploy could otherwise
# emit thousands of identical warnings for one stale price.
_warned_stale_models: set[str] = set()


def stale_models(as_of: date | None = None, max_age_days: int = STALE_PRICE_MAX_AGE_DAYS) -> list[str]:
    reference = as_of or date.today()
    stale = []
    for model, rates in MODEL_RATES_PER_MILLION_USD.items():
        verified_at = date.fromisoformat(rates["verified_at"])
        if (reference - verified_at).days > max_age_days:
            stale.append(model)
    return stale


def cost_for_usage(model: str, prompt_tokens: int, completion_tokens: int) -> float:
    rates = MODEL_RATES_PER_MILLION_USD[model]
    if model not in _warned_stale_models and model in stale_models():
        logger.warning(
            "price for %s was last verified on %s, more than %d days ago - "
            "confirm it's still accurate against the provider's pricing page",
            model,
            rates["verified_at"],
            STALE_PRICE_MAX_AGE_DAYS,
        )
        _warned_stale_models.add(model)
    return (prompt_tokens * rates["input"] + completion_tokens * rates["output"]) / 1_000_000


def base_cap_for_plan(plan: str) -> float:
    if plan in PLAN_CAP_OVERRIDE_USD:
        return PLAN_CAP_OVERRIDE_USD[plan]
    price = PLAN_MONTHLY_PRICE_USD.get(plan)
    if price is None:
        return 0.0
    return price * CAP_FRACTION_OF_PRICE


def monthly_cap_for_installation(base_cap_usd: float, extra_seats: int) -> float:
    return base_cap_usd + EXTRA_SEAT_LLM_CAP_USD * extra_seats


# The cap itself is a worst-case abuse ceiling, not a target - see
# CAP_FRACTION_OF_PRICE. This is a much lower bar: a signal that real usage
# is starting to approach that ceiling, worth a log line so someone notices
# before an installation actually hits it, not proof of a problem on its own.
WARN_FRACTION_OF_CAP = 0.3


def crossed_spend_warning_threshold(
    previous_total_usd: float, new_total_usd: float, monthly_cap_usd: float
) -> bool:
    """Whether this specific increment is the one that pushed spend past
    WARN_FRACTION_OF_CAP of the cap.

    Edge-triggered on the previous/new pair rather than just checking
    new_total_usd, so record_llm_spend logs once per crossing instead of on
    every call for the rest of the month once an installation is over the
    threshold.
    """
    if monthly_cap_usd <= 0:
        return False
    threshold = WARN_FRACTION_OF_CAP * monthly_cap_usd
    return previous_total_usd < threshold <= new_total_usd
