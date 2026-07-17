from app_server.db import set_installation_plan, upsert_installation


async def handle_marketplace_event(payload: dict, pool) -> None:
    action = payload.get("action")
    purchase = payload["marketplace_purchase"]
    account = purchase["account"]
    installation_id = account["id"]
    account_login = account["login"]

    await upsert_installation(pool, installation_id, account_login)

    if action in ("purchased", "changed"):
        await set_installation_plan(pool, installation_id, purchase["plan"]["name"])
    elif action == "cancelled":
        await set_installation_plan(pool, installation_id, "free")
