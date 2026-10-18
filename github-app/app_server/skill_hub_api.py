import asyncio
import hashlib
import secrets

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel

from app_server.admin import TokenLabel, _bearer_github_token, _fetch_github_user
from app_server.db import (
    create_personal_token,
    get_custom_skill,
    get_or_create_personal_identity,
    get_personal_identity_by_token_hash,
    list_custom_skills,
    upsert_custom_skill,
)

skill_hub_router = APIRouter()


class CreatePersonalTokenRequest(BaseModel):
    label: str = TokenLabel


class PutCustomSkillRequest(BaseModel):
    content: str


@skill_hub_router.post("/v1/personal-tokens")
async def mint_personal_token(request: Request, body: CreatePersonalTokenRequest):
    # Deliberately independent of any Aletheore installation: this mints an
    # identity token for ANY GitHub account, unlike the paid-only
    # /v1/cli-tokens flow (admin.py's create_cli_token), which requires an
    # administered, paid installation. No installation lookup and no plan
    # check belong anywhere in this path.
    github_token = _bearer_github_token(request)
    user = await asyncio.to_thread(_fetch_github_user, github_token)
    pool = request.app.state.db_pool
    identity = await get_or_create_personal_identity(pool, user["id"], user["login"])
    raw_token = secrets.token_urlsafe(32)
    token_hash = hashlib.sha256(raw_token.encode()).hexdigest()
    await create_personal_token(pool, identity["id"], token_hash, body.label)
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
    return await upsert_custom_skill(request.app.state.db_pool, identity["id"], name, body.content)
