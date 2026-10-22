import asyncio
import hashlib
import logging
import re
import secrets

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field, field_validator

from app_server import admin as _admin
from app_server.admin import TokenLabel, _bearer_github_token, _fetch_github_user
from app_server.config import get_settings
from app_server.db import (
    create_personal_token_within_limit,
    delete_custom_skill,
    get_custom_skill,
    get_or_create_personal_identity,
    get_personal_identity_by_token_hash,
    list_custom_skills,
    revoke_all_personal_tokens,
    revoke_personal_token,
    upsert_custom_skill,
)
from app_server.ingest_limits import parse_validated_body_after_auth
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

    @field_validator("content")
    @classmethod
    def _no_nul_bytes(cls, value: str) -> str:
        # Postgres text cannot hold a NUL byte: it would surface as a 500 that the client retries on every sync.
        if "\x00" in value:
            raise ValueError("content must not contain NUL bytes")
        return value


def _github_token_issued_to_aletheore(github_token: str) -> bool:
    """True only if this GitHub token was issued to the Aletheore app itself.

    `/user` answers for any valid GitHub token, including a leaked personal
    access token or a token from an unrelated OAuth app. Minting a
    non-expiring Aletheore token from one of those would let whoever holds it
    write custom skills into the victim's account. GitHub's "check a token"
    endpoint reports which app a user token belongs to, so we require it to be
    ours.
    """
    settings = get_settings()
    response = _admin._github_http_client().post(
        f"/applications/{settings.github_client_id}/token",
        auth=(settings.github_client_id, settings.github_client_secret),
        json={"access_token": github_token},
        headers={"Accept": "application/vnd.github+json"},
    )
    if response.status_code != 200:
        # 404: GitHub does not know this token as one issued to this app. 401/403: our client id and secret were
        # rejected (a misconfigured server), which would make every login fail. Status code only, never the token.
        logger.warning("GitHub check-a-token returned %s for the personal-token mint", response.status_code)
        return False
    app = response.json().get("app") or {}
    return app.get("client_id") == settings.github_client_id


@skill_hub_router.post("/v1/personal-tokens")
async def mint_personal_token(request: Request):
    # Deliberately independent of any Aletheore installation: this mints an
    # identity token for ANY GitHub account, unlike the paid-only
    # /v1/cli-tokens flow (admin.py's create_cli_token), which requires an
    # administered, paid installation. No installation lookup and no plan
    # check belong anywhere in this path.
    github_token = _bearer_github_token(request)
    if not await asyncio.to_thread(_github_token_issued_to_aletheore, github_token):
        raise HTTPException(status_code=401, detail="that GitHub token was not issued to Aletheore - run `aletheore login`")
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

    # Parsed after the GitHub token check, not bound as a handler parameter, so an
    # unauthenticated caller never pays for body parsing (see ingest_limits).
    body = await parse_validated_body_after_auth(request, CreatePersonalTokenRequest)

    pool = request.app.state.db_pool
    identity = await get_or_create_personal_identity(pool, user["id"], user["login"])
    raw_token = secrets.token_urlsafe(32)
    token_hash = hashlib.sha256(raw_token.encode()).hexdigest()
    # Never rejects: at the cap this rotates out the single oldest token
    # instead of 409ing (see create_personal_token_within_limit).
    await create_personal_token_within_limit(pool, identity["id"], token_hash, body.label)
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


def _bearer_token_hash(request: Request) -> str:
    # Same bearer-extraction + hashing logic _authenticate_personal_token
    # already does inline, pulled out so the revoke-self endpoint can get the
    # raw token's hash without changing _authenticate_personal_token's
    # existing return contract (just the identity dict), which the three
    # custom-skills routes above depend on.
    auth_header = request.headers.get("Authorization", "")
    if not auth_header.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="missing bearer token")
    raw_token = auth_header.removeprefix("Bearer ")
    return hashlib.sha256(raw_token.encode()).hexdigest()


@skill_hub_router.delete("/v1/personal-tokens", status_code=204)
async def revoke_own_personal_token(request: Request):
    await _authenticate_personal_token(request)  # 401s on an already-invalid token before we touch anything
    token_hash = _bearer_token_hash(request)
    await revoke_personal_token(request.app.state.db_pool, token_hash)


@skill_hub_router.delete("/v1/personal-tokens/all", status_code=204)
async def revoke_all_personal_tokens_route(request: Request):
    identity = await _authenticate_personal_token(request)
    await revoke_all_personal_tokens(request.app.state.db_pool, identity["id"])


@skill_hub_router.get("/v1/custom-skills")
async def list_custom_skills_route(request: Request):
    identity = await _authenticate_personal_token(request)
    skills = await list_custom_skills(request.app.state.db_pool, identity["id"])
    # account_id lets a client notice that this machine was last synced with a
    # different Aletheore account (it must not delete or upload across them).
    return {"skills": skills, "account_id": str(identity["id"])}


@skill_hub_router.get("/v1/custom-skills/{name}")
async def get_custom_skill_route(request: Request, name: str):
    identity = await _authenticate_personal_token(request)
    skill = await get_custom_skill(request.app.state.db_pool, identity["id"], name)
    if skill is None:
        raise HTTPException(status_code=404, detail="no custom skill by that name")
    return skill


@skill_hub_router.put("/v1/custom-skills/{name}")
async def put_custom_skill_route(request: Request, name: str):
    identity = await _authenticate_personal_token(request)
    if not _SAFE_SKILL_NAME.fullmatch(name):
        raise HTTPException(
            status_code=400,
            detail="invalid skill name: use 1-128 letters, digits, '-' or '_'",
        )
    # Parsed after auth, not bound as a handler parameter: a bound `body: Model` is parsed and
    # validated before the first line of the handler runs, so an unauthenticated caller would
    # pay for it (see ingest_limits.parse_validated_body_after_auth).
    body = await parse_validated_body_after_auth(request, PutCustomSkillRequest)
    return await upsert_custom_skill(request.app.state.db_pool, identity["id"], name, body.content)


@skill_hub_router.delete("/v1/custom-skills/{name}", status_code=204)
async def delete_custom_skill_route(request: Request, name: str):
    identity = await _authenticate_personal_token(request)
    deleted = await delete_custom_skill(request.app.state.db_pool, identity["id"], name)
    if not deleted:
        raise HTTPException(status_code=404, detail="no custom skill by that name")
