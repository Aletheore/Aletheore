from app_server.db import add_installation_member, get_installation, set_installation_plan, upsert_installation


async def handle_marketplace_event(payload: dict, pool, redis_url: str, queue=None) -> None:
    action = payload.get("action")
    purchase = payload["marketplace_purchase"]
    account = purchase["account"]
    installation_id = account["id"]
    account_login = account["login"]

    previous = await get_installation(pool, installation_id)
    previous_plan = previous["plan"] if previous is not None else "free"

    await upsert_installation(pool, installation_id, account_login)

    if action in ("purchased", "changed"):
        new_plan = purchase["plan"]["name"]
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
