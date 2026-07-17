import json
from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException, Request

from app_server.config import get_settings
from app_server.dashboard import dashboard_router
from app_server.db import create_pool
from app_server.signature import verify_signature
from app_server.webhooks.installation import handle_installation_event

settings = get_settings()


@asynccontextmanager
async def lifespan(app: FastAPI):
    app.state.db_pool = await create_pool(settings.database_url)
    yield
    await app.state.db_pool.close()


app = FastAPI(lifespan=lifespan)
app.include_router(dashboard_router)


@app.post("/webhook")
async def webhook(request: Request):
    body = await request.body()
    signature = request.headers.get("X-Hub-Signature-256", "")
    if not verify_signature(body, signature, settings.github_webhook_secret):
        raise HTTPException(status_code=401, detail="invalid signature")

    event = request.headers.get("X-GitHub-Event", "")
    payload = json.loads(body)
    pool = request.app.state.db_pool

    if event in ("installation", "installation_repositories"):
        await handle_installation_event(event, payload, pool)
    elif event == "marketplace_purchase":
        from app_server.webhooks.marketplace import handle_marketplace_event

        await handle_marketplace_event(payload, pool)
    elif event == "pull_request":
        from app_server.webhooks.pull_request import handle_pull_request_event

        await handle_pull_request_event(payload, settings.redis_url)

    return {"ok": True}
