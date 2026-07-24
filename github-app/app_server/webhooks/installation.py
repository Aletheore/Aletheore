import shutil
from pathlib import Path

from app_server.db import delete_installation, upsert_installation

MIRROR_ROOT = Path("/var/aletheore/mirrors")


async def handle_installation_event(event_name: str, payload: dict, pool) -> None:
    action = payload.get("action")
    installation = payload["installation"]
    installation_id = installation["id"]
    account_login = installation["account"]["login"]

    if event_name == "installation" and action == "deleted":
        await delete_installation(pool, installation_id)
        shutil.rmtree(MIRROR_ROOT / str(installation_id), ignore_errors=True)
        return

    await upsert_installation(pool, installation_id, account_login)
