import json
import logging
import time
import uuid
from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse

from app_server.admin import admin_router
from app_server.auth import auth_router
from app_server.config import get_settings
from app_server.dashboard import dashboard_router
from app_server.db import create_pool
from app_server.frontend import frontend_router
from app_server.logging_config import configure_json_logging
from app_server.managed_audit_api import managed_audit_router
from app_server.metrics import metrics_router
from app_server.signature import verify_signature
from app_server.webhooks.installation import handle_installation_event

configure_json_logging()
access_logger = logging.getLogger("app_server.access")

settings = get_settings()


@asynccontextmanager
async def lifespan(app: FastAPI):
    app.state.db_pool = await create_pool(settings.database_url)
    yield
    await app.state.db_pool.close()


app = FastAPI(lifespan=lifespan)
app.include_router(dashboard_router)
app.include_router(auth_router)
app.include_router(admin_router)
app.include_router(managed_audit_router)
app.include_router(metrics_router)
app.include_router(frontend_router)


@app.middleware("http")
async def log_requests(request: Request, call_next):
    request_id = str(uuid.uuid4())
    start = time.monotonic()
    response = await call_next(request)
    duration_ms = round((time.monotonic() - start) * 1000, 2)
    access_logger.info(
        "request completed",
        extra={
            "request_id": request_id,
            "method": request.method,
            "path": request.url.path,
            "status_code": response.status_code,
            "duration_ms": duration_ms,
        },
    )
    response.headers["X-Request-ID"] = request_id
    return response


@app.get("/healthz")
async def healthz(request: Request):
    checks = {"database": "ok", "redis": "ok"}

    try:
        await request.app.state.db_pool.fetchval("SELECT 1")
    except Exception:
        checks["database"] = "error"

    try:
        from redis import Redis

        Redis.from_url(settings.redis_url).ping()
    except Exception:
        checks["redis"] = "error"

    healthy = all(value == "ok" for value in checks.values())
    return JSONResponse(
        status_code=200 if healthy else 503,
        content={"status": "ok" if healthy else "error", "checks": checks},
    )


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

        await handle_marketplace_event(payload, pool, settings.redis_url)
    elif event == "pull_request":
        from app_server.webhooks.pull_request import handle_pull_request_event

        await handle_pull_request_event(payload, settings.redis_url)
    elif event == "issue_comment":
        from app_server.webhooks.issue_comment import handle_issue_comment_event

        await handle_issue_comment_event(payload, settings.redis_url)

    return {"ok": True}
