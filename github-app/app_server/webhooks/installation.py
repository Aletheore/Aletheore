from app_server.db import delete_installation, upsert_installation


async def handle_installation_event(event_name: str, payload: dict, pool) -> None:
    action = payload.get("action")
    installation = payload["installation"]
    installation_id = installation["id"]
    account_login = installation["account"]["login"]

    if event_name == "installation" and action == "deleted":
        await delete_installation(pool, installation_id)
        return

    await upsert_installation(pool, installation_id, account_login)
