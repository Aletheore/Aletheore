import hashlib
import secrets

import httpx
from fastapi import APIRouter, HTTPException, Request

from app_server.auth import get_current_session
from app_server.db import (
    count_active_tokens,
    create_api_token,
    get_installation,
    get_max_tokens,
    list_api_tokens,
    revoke_api_token,
    set_webhook_url,
)

admin_router = APIRouter()

BRANCH_PROTECTION_DISCLOSURE = (
    "Aletheore reports a Check Run result on new secrets found - it does not and cannot "
    "unilaterally block a merge. To require it, mark \"Aletheore secrets check\" as a "
    "required status check in this repository's branch protection settings."
)


def _github_http_client() -> httpx.Client:
    return httpx.Client(base_url="https://api.github.com")


async def _repo_installation_id(pool, org: str, repo: str) -> int:
    row = await pool.fetchrow(
        """
        SELECT DISTINCT installation_id
        FROM repo_history
        WHERE repo_full_name = $1
        LIMIT 1
        """,
        f"{org}/{repo}",
    )
    if row is None:
        raise HTTPException(status_code=404, detail="no such repo")
    return row["installation_id"]


async def _require_admin_installation(request: Request, org: str, repo: str) -> dict:
    session = await get_current_session(request)
    if session is None:
        raise HTTPException(status_code=401, detail="login required")

    pool = request.app.state.db_pool
    installation_id = await _repo_installation_id(pool, org, repo)
    response = _github_http_client().get(
        "/user/installations",
        headers={
            "Authorization": f"Bearer {session['github_access_token']}",
            "Accept": "application/vnd.github+json",
        },
    )
    response.raise_for_status()
    administered_ids = {item["id"] for item in response.json().get("installations", [])}
    if installation_id not in administered_ids:
        raise HTTPException(status_code=403, detail="you do not administer this installation")

    installation = await get_installation(pool, installation_id)
    if installation is None:
        raise HTTPException(status_code=404, detail="installation not found")
    if installation["plan"] == "free":
        raise HTTPException(status_code=402, detail="this feature requires a paid plan")
    return installation


@admin_router.get("/admin/{org}/{repo}")
async def admin_page(org: str, repo: str, request: Request):
    installation = await _require_admin_installation(request, org, repo)
    tokens = await list_api_tokens(request.app.state.db_pool, installation["installation_id"])
    return {
        "installation": installation,
        "tokens": tokens,
        "branch_protection_disclosure": BRANCH_PROTECTION_DISCLOSURE,
    }


@admin_router.post("/admin/{org}/{repo}/tokens")
async def generate_token(org: str, repo: str, request: Request):
    installation = await _require_admin_installation(request, org, repo)
    session = await get_current_session(request)
    body = await request.json()
    label = body["label"]
    pool = request.app.state.db_pool
    installation_id = installation["installation_id"]

    max_tokens = await get_max_tokens(pool, installation_id)
    if await count_active_tokens(pool, installation_id) >= max_tokens:
        raise HTTPException(status_code=409, detail=f"token limit reached ({max_tokens})")

    raw_token = secrets.token_urlsafe(32)
    token_hash = hashlib.sha256(raw_token.encode()).hexdigest()
    await create_api_token(pool, installation_id, token_hash, label, session["github_login"])
    token_id = (await list_api_tokens(pool, installation_id))[0]["id"]
    return {"token": raw_token, "id": token_id, "label": label}


@admin_router.delete("/admin/{org}/{repo}/tokens/{token_id}")
async def revoke_token(org: str, repo: str, token_id: int, request: Request):
    installation = await _require_admin_installation(request, org, repo)
    await revoke_api_token(request.app.state.db_pool, installation["installation_id"], token_id)
    return {"ok": True}


@admin_router.put("/admin/{org}/{repo}/webhook-url")
async def set_webhook_url_route(org: str, repo: str, request: Request):
    installation = await _require_admin_installation(request, org, repo)
    body = await request.json()
    await set_webhook_url(
        request.app.state.db_pool,
        installation["installation_id"],
        body.get("webhook_url"),
    )
    return {"ok": True}
