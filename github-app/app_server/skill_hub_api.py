import asyncio
import hashlib
import secrets

from fastapi import APIRouter, Request
from pydantic import BaseModel

from app_server.admin import TokenLabel, _bearer_github_token, _fetch_github_user
from app_server.db import create_personal_token, get_or_create_personal_identity

skill_hub_router = APIRouter()


class CreatePersonalTokenRequest(BaseModel):
    label: str = TokenLabel


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
