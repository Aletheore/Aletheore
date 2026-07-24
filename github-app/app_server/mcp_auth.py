"""Bearer-token auth for the hosted MCP app."""

import contextvars
import hashlib

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import JSONResponse

from app_server.config import get_settings
from app_server.db import get_installation_by_token_hash_for_mcp

CURRENT_INSTALLATION_ID: contextvars.ContextVar[int | None] = contextvars.ContextVar(
    "current_installation_id", default=None
)


class McpAuthMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        auth_header = request.headers.get("authorization", "")
        if not auth_header.lower().startswith("bearer "):
            return JSONResponse({"error": "missing bearer token"}, status_code=401)

        raw_token = auth_header[len("bearer ") :].strip()
        token_hash = hashlib.sha256(raw_token.encode("utf-8")).hexdigest()
        installation = get_installation_by_token_hash_for_mcp(get_settings().database_url, token_hash)
        if installation is None:
            return JSONResponse({"error": "invalid token"}, status_code=401)
        if installation["plan"] == "free":
            return JSONResponse({"error": "hosted MCP requires a paid plan"}, status_code=402)

        reset_token = CURRENT_INSTALLATION_ID.set(installation["installation_id"])
        try:
            return await call_next(request)
        finally:
            CURRENT_INSTALLATION_ID.reset(reset_token)
