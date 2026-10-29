"""Narrow authenticated budget gate for the website. Never expose Redis/EVAL publicly."""

import asyncio
import hashlib
import hmac
import json
import os
import functools
from pathlib import Path
from typing import Literal

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field, ValidationError

from redis.asyncio import Redis, ConnectionPool
from redis.asyncio.connection import parse_url
from redis.backoff import NoBackoff
from redis.asyncio.retry import Retry
from starlette.requests import ClientDisconnect

from app_server.config import get_settings

companion_limits_router = APIRouter()
PREFIX = "aletheore:{companion-budget}"
LEDGER = f"{PREFIX}:ledger"
SCRIPTS = json.loads(
    Path(__file__).with_name("companion_limit_scripts.json").read_text(encoding="utf-8")
)

POLICY = hashlib.sha256(
    "".join(
        SCRIPTS[name] for name in ("RESERVE_SCRIPT", "SETTLE_SCRIPT", "DISABLE_SCRIPT")
    ).encode()
).hexdigest()


@functools.lru_cache(maxsize=1)
def get_redis_client() -> Redis:
    """Independent bounded async pool: outages cannot occupy executor threads.

    Override URL timeout/retry parameters deliberately. Never retry an ambiguous
    admission or depend on the shared queue client's retry defaults.
    """
    options = parse_url(get_settings().redis_url)
    options.update(
        socket_connect_timeout=0.5,
        socket_timeout=0.5,
        max_connections=4,
        retry=Retry(NoBackoff(), 0),
        retry_on_error=[],
    )
    return Redis(connection_pool=ConnectionPool(**options))


class LimitCommand(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    operation: Literal["reserve", "settle", "disable"]
    request_id: str = Field(
        pattern=r"^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$"
    )
    client_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    cost: int | None = Field(default=None, ge=-1, le=5_000_000)


@companion_limits_router.post("/v1/internal/companion-limits")
async def companion_limits(request: Request):
    token = os.environ.get("COMPANION_LIMIT_SERVICE_TOKEN", "").strip()
    if len(token) < 32:
        raise HTTPException(status_code=404, detail="not found")
    if not hmac.compare_digest(
        request.headers.get("authorization", "").encode(), f"Bearer {token}".encode()
    ):
        raise HTTPException(status_code=401, detail="unauthorized")
    body = bytearray()
    try:
        async with asyncio.timeout(3):
            async for chunk in request.stream():
                body.extend(chunk)
                if len(body) > 2048:
                    raise HTTPException(status_code=413, detail="request too large")
        command = LimitCommand.model_validate(json.loads(body))
    except (ValueError, ValidationError):
        raise HTTPException(status_code=400, detail="invalid limit command") from None
    except ClientDisconnect:
        raise HTTPException(status_code=400, detail="request disconnected") from None
    except TimeoutError:
        raise HTTPException(status_code=408, detail="request timed out") from None
    if (command.operation == "settle") != (command.cost is not None):
        raise HTTPException(status_code=400, detail="invalid limit command")
    reservation = f"{PREFIX}:reservation:{command.request_id}"
    active = f"{PREFIX}:active"
    client_active = f"{PREFIX}:active:{command.client_hash}"
    if command.operation == "reserve":
        script = SCRIPTS["RESERVE_SCRIPT"]
        keys = [
            LEDGER,
            f"{PREFIX}:requests",
            active,
            f"{PREFIX}:client:{command.client_hash}",
            client_active,
            reservation,
        ]
        args = [command.request_id, "5000000", command.client_hash]
    elif command.operation == "settle":
        script = SCRIPTS["SETTLE_SCRIPT"]
        keys = [LEDGER, active, client_active, reservation]
        args = [command.request_id, str(command.cost), command.client_hash]
    else:
        script = SCRIPTS["DISABLE_SCRIPT"]
        keys = [LEDGER, reservation]
        args = [command.request_id, command.client_hash]
    try:
        async with asyncio.timeout(1.5):
            result = await get_redis_client().eval(script, len(keys), *keys, *args)
    except Exception:
        # No fail-open, raw Redis diagnostic, key or caller body in errors/logs.
        raise HTTPException(
            status_code=503, detail="usage protection unavailable"
        ) from None
    return JSONResponse(
        {"result": result, "policy": POLICY}, headers={"Cache-Control": "no-store"}
    )
