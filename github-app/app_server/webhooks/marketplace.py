import logging

from app_server.db import add_installation_member, get_installation_by_account_login, set_installation_plan

logger = logging.getLogger(__name__)


def _normalize_marketplace_plan_name(raw_name: str) -> str:
    """Marketplace plan display names are configured on GitHub's own
    listing page, not by our code - `purchase["plan"]["name"]` could be
    anything an admin typed there, and the only two slugs the rest of the
    codebase understands are "free" and "air" (see paddle_pricing.py).
    Writing the raw name straight into installations.plan meant any name
    that wasn't exactly the lowercase string "free" granted full paid
    access, including a plan literally named "Free" with a capital F.

    Recognizes anything naming the paid tier ("air", case-insensitive,
    matching the product's own AIR branding) and defaults everything
    else to "free" - fail-closed, not fail-open, for a name this doesn't
    recognize. Whatever the listing's plans are actually named should be
    confirmed against this function before the Marketplace listing goes
    live (website copy still says it's pending GitHub's review as of this
    writing).
    """
    if "air" in raw_name.strip().lower():
        return "air"
    return "free"


async def handle_marketplace_event(payload: dict, pool, redis_url: str, queue=None) -> None:
    action = payload.get("action")
    purchase = payload["marketplace_purchase"]
    account = purchase["account"]
    account_login = account["login"]

    # purchase["account"]["id"] is a GitHub user/org account ID, a
    # different ID space from the GitHub App installation ID everywhere
    # else in this codebase uses - the Marketplace webhook carries no
    # installation ID at all. The only reliable correlation is by
    # account_login against a row the `installation` webhook already
    # created (relies on installations_account_login_unique, migration
    # 042, for a well-defined lookup).
    installation = await get_installation_by_account_login(pool, account_login)
    if installation is None:
        logger.warning(
            "marketplace_purchase for %s (action=%s) has no matching installation yet - skipping",
            account_login,
            action,
        )
        return
    installation_id = installation["installation_id"]
    previous_plan = installation["plan"]

    if action in ("purchased", "changed"):
        new_plan = _normalize_marketplace_plan_name(purchase["plan"]["name"])
        await set_installation_plan(pool, installation_id, new_plan)

        # Whoever completed the purchase becomes seat one, so they're never
        # locked out of their own installation's Settings by the seat check -
        # idempotent, so a plan change on an already-paid installation is a
        # harmless no-op here.
        purchaser_login = payload.get("sender", {}).get("login")
        if new_plan != "free" and purchaser_login:
            await add_installation_member(pool, installation_id, purchaser_login, purchaser_login)

        # One-time Live Wiki build, tier-independent - fires exactly once,
        # on the free -> paid transition. A paid-to-paid plan change (e.g.
        # Team -> Growth) must not re-trigger it.
        if previous_plan == "free" and new_plan != "free":
            if queue is None:
                from redis import Redis
                from rq import Queue

                queue = Queue("scans", connection=Redis.from_url(redis_url))
            queue.enqueue(
                "scan_worker.jobs.run_live_wiki_full_build_for_installation_job",
                job_timeout=60,
                installation_id=installation_id,
            )
            # One-time Docs build, same trigger and tier-independence as the
            # Live Wiki build immediately above.
            queue.enqueue(
                "scan_worker.jobs.run_live_docs_full_build_for_installation_job",
                job_timeout=60,
                installation_id=installation_id,
            )
    elif action == "cancelled":
        await set_installation_plan(pool, installation_id, "free")
