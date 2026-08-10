import asyncio
import json
import logging
import time
import uuid
from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from app_server.admin import admin_router
from app_server.auth import auth_router
from app_server.config import get_settings
from app_server.dashboard import dashboard_router
from app_server.db import claim_webhook_delivery, create_pool, release_webhook_delivery
from app_server.demo_scan_api import demo_scan_router
from app_server.error_alerts import send_error_alert
from app_server.frontend import frontend_router
from app_server.logging_config import configure_json_logging
from app_server.managed_audit_api import managed_audit_router
from app_server.metrics import metrics_router
from app_server.runtime_events import runtime_events_router
from app_server.signature import verify_signature
from app_server.telemetry import telemetry_router
from app_server.webhooks.installation import handle_installation_event
from app_server.webhooks.paddle import paddle_webhook_router

configure_json_logging()
access_logger = logging.getLogger("app_server.access")

settings = get_settings()


@asynccontextmanager
async def lifespan(app: FastAPI):
    app.state.db_pool = await create_pool(settings.database_url)
    yield
    await app.state.db_pool.close()


app = FastAPI(lifespan=lifespan)
app.add_middleware(
    CORSMiddleware,
    # Only the public demo (/v1/demo-scan) is meant to be called from
    # browser JS on a different origin - the marketing site. Everything
    # else here is same-site cookie auth or a Bearer-token API not
    # normally called from a browser, so this stays narrowly scoped
    # rather than a wildcard.
    allow_origins=["https://aletheore.com", "https://www.aletheore.com"],
    allow_methods=["GET", "POST"],
    allow_headers=["Content-Type"],
)
app.include_router(dashboard_router)
app.include_router(auth_router)
app.include_router(admin_router)
app.include_router(managed_audit_router)
app.include_router(metrics_router)
app.include_router(frontend_router)
app.include_router(paddle_webhook_router)
app.include_router(demo_scan_router)
app.include_router(telemetry_router)
app.include_router(runtime_events_router)


@app.middleware("http")
async def no_store_for_session_data(request: Request, call_next):
    # /admin and /app (but not the deliberately-public /v1/health/...)
    # carry per-installation data - API tokens, team member logins, health
    # check URLs, security findings. A browser or intermediate proxy
    # caching a response here could replay someone else's data after they
    # sign out, or on a shared machine. no-store forbids that outright.
    response = await call_next(request)
    path = request.url.path
    if path.startswith("/admin") or path.startswith("/app/"):
        response.headers["Cache-Control"] = "no-store"
    return response


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


@app.exception_handler(Exception)
async def handle_unexpected_exception(request: Request, exc: Exception) -> JSONResponse:
    # FastAPI's default HTTPException handler stays in effect for
    # HTTPException specifically (a more specific handler is already
    # registered for it) - this only ever catches something nobody
    # deliberately raised, i.e. a real bug. Previously the only way to
    # learn about one of these was reading logs after the fact.
    logging.getLogger("app_server.errors").exception(
        "unhandled exception in request",
        extra={"method": request.method, "path": request.url.path},
    )
    await asyncio.to_thread(
        send_error_alert, "app_server", exc, f"{request.method} {request.url.path}"
    )
    return JSONResponse(status_code=500, content={"detail": "internal server error"})


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

    # Required, not optional. GitHub sends X-GitHub-Delivery on every
    # delivery including pings, and treating a missing header as "just
    # process it" would hand anyone holding a captured payload a one-header
    # bypass of the replay protection below.
    delivery_id = request.headers.get("X-GitHub-Delivery", "")
    if not delivery_id:
        raise HTTPException(status_code=400, detail="missing X-GitHub-Delivery")

    event = request.headers.get("X-GitHub-Event", "")
    payload = json.loads(body)
    pool = request.app.state.db_pool

    # Claimed after the signature check, so an unauthenticated caller can't
    # poison the ledger with invented GUIDs and suppress the real deliveries
    # that follow. Also after JSON parsing, so a body that could never be
    # handled doesn't burn a GUID on its way to failing.
    if not await claim_webhook_delivery(pool, "github", delivery_id, event):
        access_logger.info(
            "duplicate webhook delivery ignored",
            extra={"delivery_id": delivery_id, "github_event": event},
        )
        return {"ok": True, "duplicate": True}

    try:
        if event in ("installation", "installation_repositories"):
            await handle_installation_event(event, payload, pool, settings.redis_url)
        elif event == "marketplace_purchase":
            from app_server.webhooks.marketplace import handle_marketplace_event

            await handle_marketplace_event(payload, pool, settings.redis_url)
        elif event == "pull_request":
            from app_server.webhooks.pull_request import handle_pull_request_event

            await handle_pull_request_event(payload, settings.redis_url)
        elif event == "push":
            from app_server.webhooks.push import handle_push_event

            await handle_push_event(payload, settings.redis_url)
        elif event == "issue_comment":
            from app_server.webhooks.issue_comment import handle_issue_comment_event

            await handle_issue_comment_event(payload, settings.redis_url)
    except Exception:
        # Hand the GUID back before failing, or GitHub's retry of this same
        # delivery would be treated as a duplicate and dropped - turning a
        # transient error into permanent event loss.
        await release_webhook_delivery(pool, "github", delivery_id)
        raise

    return {"ok": True}
