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
    "deepseek-v4-pro": {"input": 0.435, "output": 0.87, "verified_at": "2026-07-23"},
    "deepseek-v4-flash": {"input": 0.14, "output": 0.28, "verified_at": "2026-07-23"},
    "gpt-4o": {"input": 2.50, "output": 10.00, "verified_at": "2026-07-23"},
    "claude-opus-4-8": {"input": 15.00, "output": 75.00, "verified_at": "2026-07-23"},
}

STALE_PRICE_MAX_AGE_DAYS = 90

EXTRA_SEAT_MONTHLY_COST_USD = 2.00

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


def monthly_cap_for_installation(base_cap_usd: float, extra_seats: int) -> float:
    return base_cap_usd + EXTRA_SEAT_MONTHLY_COST_USD * extra_seats
