"""Charge a customer's credit for remote embedding tokens.

Only the DeepInfra embedder costs anything: the self-hosted Jina sidecar reports
zero tokens, so none of this runs for it. Spend is drawn from the same balance and
ledger as every other LLM feature (reserve_llm_spend / record_llm_spend), under its
own `feature` label so it shows up separately in the per-feature breakdown.

Best-effort by design. The embedding has already happened and the amounts are tiny
(about $0.000015 for a 5,000-character diff), so a billing failure must never fail a
review, a docs build or an index request; it is logged instead.
"""

import logging

from app_server.hosted_embedder import deepinfra_model
from app_server.llm_cost import cost_for_usage
from scan_worker.db import get_installation, record_llm_spend, reserve_llm_spend

logger = logging.getLogger(__name__)

FEATURE_CACHE = "embedding_cache"
FEATURE_HOSTED = "hosted_embeddings"


def charge_embedding_tokens(dsn: str, installation_id: int, tokens: int, feature: str) -> float:
    """Draw the cost of `tokens` from the installation's credit and ledger it.
    Returns the dollars charged (0.0 when there was nothing to charge or billing failed)."""
    if tokens <= 0:
        return 0.0
    try:
        cost = cost_for_usage(deepinfra_model(), tokens, 0)
        if not reserve_llm_spend(dsn, installation_id, cost):
            # The embedding is already done, so its cost is sunk. Take what is left rather
            # than leaving the balance overstated (same drain-to-zero tail as the review job).
            row = get_installation(dsn, installation_id)
            if row is not None:
                remaining = float(row.get("base_credit_remaining_usd", 0)) + float(
                    row.get("topup_credit_balance_usd", 0)
                )
                if remaining > 0:
                    reserve_llm_spend(dsn, installation_id, remaining)
        # The ledger records what the embedding really cost, even when the balance could
        # only cover part of it: same as the review job, so per-feature totals stay true.
        record_llm_spend(dsn, installation_id, cost, feature=feature)
    except Exception as exc:  # noqa: BLE001 - billing bookkeeping must never fail the caller
        logger.warning(
            "could not charge embedding spend for installation=%s (%s)", installation_id, type(exc).__name__
        )
        return 0.0
    return cost
