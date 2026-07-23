# Cache-miss, list-price rates only - provider list prices, confirm still
# current before relying on them for real spend accounting. Overestimating
# cost is the safe direction for a hard cap, so when in doubt round up.
MODEL_RATES_PER_MILLION_USD = {
    "deepseek-v4-pro": {"input": 0.435, "output": 0.87},
    "deepseek-v4-flash": {"input": 0.14, "output": 0.28},
    "gpt-4o": {"input": 2.50, "output": 10.00},
    "claude-opus-4-8": {"input": 15.00, "output": 75.00},
}

EXTRA_SEAT_MONTHLY_COST_USD = 2.00


def cost_for_usage(model: str, prompt_tokens: int, completion_tokens: int) -> float:
    rates = MODEL_RATES_PER_MILLION_USD[model]
    return (prompt_tokens * rates["input"] + completion_tokens * rates["output"]) / 1_000_000


def monthly_cap_for_installation(base_cap_usd: float, extra_seats: int) -> float:
    return base_cap_usd + EXTRA_SEAT_MONTHLY_COST_USD * extra_seats
