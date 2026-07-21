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
    set_health_check_config,
    set_webhook_url,
)
from app_server.url_validation import UnsafeURLError, validate_external_https_url

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


async def _administered_installation_ids(github_token: str) -> set[int]:
    response = _github_http_client().get(
        "/user/installations",
        headers={
            "Authorization": f"Bearer {github_token}",
            "Accept": "application/vnd.github+json",
        },
    )
    response.raise_for_status()
    return {item["id"] for item in response.json().get("installations", [])}


def _bearer_github_token(request: Request) -> str:
    auth_header = request.headers.get("Authorization", "")
    if not auth_header.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="missing bearer token")
    return auth_header.removeprefix("Bearer ")


async def _require_admin_installation(request: Request, org: str, repo: str) -> dict:
    session = await get_current_session(request)
    if session is None:
        raise HTTPException(status_code=401, detail="login required")

    pool = request.app.state.db_pool
    installation_id = await _repo_installation_id(pool, org, repo)
    administered_ids = await _administered_installation_ids(session["github_access_token"])
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
    webhook_url = body.get("webhook_url")
    if webhook_url:
        try:
            validate_external_https_url(webhook_url)
        except UnsafeURLError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
    await set_webhook_url(
        request.app.state.db_pool,
        installation["installation_id"],
        webhook_url,
    )
    return {"ok": True}


@admin_router.put("/admin/{org}/{repo}/health-check-url")
async def set_health_check_config_route(org: str, repo: str, request: Request):
    installation = await _require_admin_installation(request, org, repo)
    body = await request.json()
    health_check_base_url = body.get("health_check_base_url")
    if health_check_base_url:
        try:
            validate_external_https_url(health_check_base_url)
        except UnsafeURLError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
    await set_health_check_config(
        request.app.state.db_pool,
        installation["installation_id"],
        health_check_base_url,
        body.get("health_check_latency_threshold_ms"),
    )
    return {"ok": True}


@admin_router.get("/v1/my-installations")
async def my_installations(request: Request):
    github_token = _bearer_github_token(request)
    administered_ids = await _administered_installation_ids(github_token)
    rows = await request.app.state.db_pool.fetch(
        """
        SELECT installation_id, account_login
        FROM installations
        WHERE installation_id = ANY($1::bigint[]) AND plan != 'free'
        ORDER BY account_login ASC, installation_id ASC
        """,
        list(administered_ids),
    )
    return {"installations": [dict(row) for row in rows]}


@admin_router.post("/v1/cli-tokens")
async def create_cli_token(request: Request):
    github_token = _bearer_github_token(request)
    body = await request.json()
    installation_id = body["installation_id"]
    label = body["label"]

    administered_ids = await _administered_installation_ids(github_token)
    if installation_id not in administered_ids:
        raise HTTPException(status_code=403, detail="you do not administer this installation")

    pool = request.app.state.db_pool
    installation = await get_installation(pool, installation_id)
    if installation is None:
        raise HTTPException(status_code=404, detail="installation not found")
    if installation["plan"] == "free":
        raise HTTPException(status_code=402, detail="this feature requires a paid plan")

    max_tokens = await get_max_tokens(pool, installation_id)
    if await count_active_tokens(pool, installation_id) >= max_tokens:
        raise HTTPException(status_code=409, detail=f"token limit reached ({max_tokens})")

    raw_token = secrets.token_urlsafe(32)
    token_hash = hashlib.sha256(raw_token.encode()).hexdigest()
    await create_api_token(pool, installation_id, token_hash, label, installation["account_login"])
    token_id = (await list_api_tokens(pool, installation_id))[0]["id"]
    return {"token": raw_token, "id": token_id, "label": label}
