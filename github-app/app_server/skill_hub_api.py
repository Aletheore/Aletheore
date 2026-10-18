import asyncio
import hashlib
import logging
import re
import secrets

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field

from app_server.admin import TokenLabel, _bearer_github_token, _fetch_github_user
from app_server.db import (
    MAX_PERSONAL_TOKENS_PER_IDENTITY,
    create_personal_token_within_limit,
    get_custom_skill,
    get_or_create_personal_identity,
    get_personal_identity_by_token_hash,
    list_custom_skills,
    upsert_custom_skill,
)
from app_server.rate_limit import is_rate_limited
from app_server.redis_client import get_redis_client

logger = logging.getLogger(__name__)

skill_hub_router = APIRouter()

# Same allowed charset as the CLI's skill_hub.sanitize_skill_name
# (_SAFE_SKILL_NAME = [A-Za-z0-9_-]+, which also rules out '.', path
# separators and NUL), plus a length cap the client doesn't have. The server
# must not rely on the client having validated the name.
_SAFE_SKILL_NAME = re.compile(r"[A-Za-z0-9_-]{1,128}")

# A skill is a text instruction file; 256 KB (in characters) is far beyond
# any real one and bounds what one PUT can make us store.
MAX_CUSTOM_SKILL_CONTENT_CHARS = 256 * 1024

# Per GitHub account (keyed on the numeric user id, not IP): generous for
# real logins, and still a hard stop on a minting loop.
PERSONAL_TOKEN_RATE_LIMIT = 10
PERSONAL_TOKEN_RATE_LIMIT_WINDOW_SECONDS = 3600


class CreatePersonalTokenRequest(BaseModel):
    label: str = TokenLabel


class PutCustomSkillRequest(BaseModel):
    content: str = Field(max_length=MAX_CUSTOM_SKILL_CONTENT_CHARS)


@skill_hub_router.post("/v1/personal-tokens")
async def mint_personal_token(request: Request, body: CreatePersonalTokenRequest):
    # Deliberately independent of any Aletheore installation: this mints an
    # identity token for ANY GitHub account, unlike the paid-only
    # /v1/cli-tokens flow (admin.py's create_cli_token), which requires an
    # administered, paid installation. No installation lookup and no plan
    # check belong anywhere in this path.
    github_token = _bearer_github_token(request)
    user = await asyncio.to_thread(_fetch_github_user, github_token)

    try:
        # Sync redis-py client: run off the event loop, same as the other
        # is_rate_limited call sites (admin.py's deletion-OTP limit).
        rate_limited = await asyncio.to_thread(
            is_rate_limited,
            get_redis_client(),
            f"ratelimit:personal-token:{user['id']}",
            PERSONAL_TOKEN_RATE_LIMIT,
            PERSONAL_TOKEN_RATE_LIMIT_WINDOW_SECONDS,
        )
    except Exception as exc:  # noqa: BLE001
        logger.warning("personal token rate limit check failed (%s); allowing request", exc)
        rate_limited = False
    if rate_limited:
        raise HTTPException(status_code=429, detail="too many token requests - try again later")

    pool = request.app.state.db_pool
    identity = await get_or_create_personal_identity(pool, user["id"], user["login"])
    raw_token = secrets.token_urlsafe(32)
    token_hash = hashlib.sha256(raw_token.encode()).hexdigest()
    token_id = await create_personal_token_within_limit(pool, identity["id"], token_hash, body.label)
    if token_id is None:
        raise HTTPException(
            status_code=409, detail=f"token limit reached ({MAX_PERSONAL_TOKENS_PER_IDENTITY})"
        )
    return {"token": raw_token}


async def _authenticate_personal_token(request: Request) -> dict:
    # Mirrors managed_audit_api.py's _authenticate_token exactly, but
    # resolves to a personal_identity row via a personal API token instead
    # of an installation via an api_token - these are two independent token
    # spaces (see get_personal_identity_by_token_hash vs
    # get_installation_by_token_hash), so a revoked/foreign installation
    # token must never authenticate here, and vice versa.
    auth_header = request.headers.get("Authorization", "")
    if not auth_header.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="missing bearer token")

    raw_token = auth_header.removeprefix("Bearer ")
    token_hash = hashlib.sha256(raw_token.encode()).hexdigest()
    identity = await get_personal_identity_by_token_hash(request.app.state.db_pool, token_hash)
    if identity is None:
        raise HTTPException(status_code=401, detail="invalid or revoked token")
    return identity


@skill_hub_router.get("/v1/custom-skills")
async def list_custom_skills_route(request: Request):
    identity = await _authenticate_personal_token(request)
    skills = await list_custom_skills(request.app.state.db_pool, identity["id"])
    return {"skills": skills}


@skill_hub_router.get("/v1/custom-skills/{name}")
async def get_custom_skill_route(request: Request, name: str):
    identity = await _authenticate_personal_token(request)
    skill = await get_custom_skill(request.app.state.db_pool, identity["id"], name)
    if skill is None:
        raise HTTPException(status_code=404, detail="no custom skill by that name")
    return skill


@skill_hub_router.put("/v1/custom-skills/{name}")
async def put_custom_skill_route(request: Request, name: str, body: PutCustomSkillRequest):
    identity = await _authenticate_personal_token(request)
    if not _SAFE_SKILL_NAME.fullmatch(name):
        raise HTTPException(
            status_code=400,
            detail="invalid skill name: use 1-128 letters, digits, '-' or '_'",
        )
    return await upsert_custom_skill(request.app.state.db_pool, identity["id"], name, body.content)
