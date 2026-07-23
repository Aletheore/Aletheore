import hashlib
import secrets

import httpx
from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field

from app_server.auth import get_current_session
from app_server.db import (
    DEFAULT_HEALTH_CHECK_TARGET_LIMIT,
    DEFAULT_SEAT_LIMIT,
    INCLUDED_HEALTH_CHECK_TARGETS,
    INCLUDED_SEATS,
    add_health_check_target,
    add_installation_member,
    count_active_tokens,
    count_health_check_targets,
    count_installation_members,
    create_api_token,
    get_extra_seats,
    get_installation,
    get_max_tokens,
    is_installation_member,
    list_api_tokens,
    list_health_check_targets,
    list_installation_members,
    remove_health_check_target,
    remove_installation_member,
    revoke_api_token,
    set_webhook_url,
)
from app_server.url_validation import UnsafeURLError, validate_external_https_url

admin_router = APIRouter()

# No control characters (including newlines/tabs) or DEL - a label is a
# single line of display text stored verbatim and shown back in the admin
# dashboard and token list; letting one carry a newline or embedded escape
# sequence risks log/UI injection for no legitimate benefit.
_LABEL_PATTERN = r"^[^\x00-\x1f\x7f]+$"
TokenLabel = Field(min_length=1, max_length=100, pattern=_LABEL_PATTERN)


class GenerateTokenRequest(BaseModel):
    label: str = TokenLabel


class SetWebhookURLRequest(BaseModel):
    webhook_url: str | None = None


class AddHealthCheckTargetRequest(BaseModel):
    label: str = Field(min_length=1, max_length=100, pattern=_LABEL_PATTERN)
    base_url: str
    latency_threshold_ms: int | None = None


class CreateCliTokenRequest(BaseModel):
    installation_id: int
    label: str = TokenLabel


# GitHub username rules: alphanumeric segments joined by single hyphens,
# can't start/end with one. (Pydantic's Rust regex engine has no
# look-around, so this is segment-based rather than a lookahead pattern -
# still rejects leading/trailing/doubled hyphens.)
_GITHUB_LOGIN_PATTERN = r"^[A-Za-z0-9]+(-[A-Za-z0-9]+)*$"


class AddMemberRequest(BaseModel):
    github_login: str = Field(min_length=1, max_length=39, pattern=_GITHUB_LOGIN_PATTERN)


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


async def _require_seat_if_paid(pool, installation: dict, github_login: str) -> None:
    """Paid installations gate on a seat, not just raw GitHub admin rights -
    GitHub's own "who can manage this installation" set is an org-side
    setting Aletheore doesn't control, and in many orgs is every Owner, so
    relying on it alone would let an unlimited number of people ride free
    on one purchase. Free plans skip this entirely - there's no seat
    revenue to protect there.

    If nobody has ever been seated yet (a paid installation from before
    seats existed, or the purchase webhook hasn't landed), the first
    verified GitHub admin to show up becomes seat one rather than every
    such customer being locked out of their own account.
    """
    if installation["plan"] == "free":
        return
    if await count_installation_members(pool, installation["installation_id"]) == 0:
        await add_installation_member(pool, installation["installation_id"], github_login, github_login)
        return
    if not await is_installation_member(pool, installation["installation_id"], github_login):
        raise HTTPException(
            status_code=403,
            detail="you administer this installation on GitHub, but haven't been added as a seat yet - "
            "ask a teammate to add you in Settings",
        )


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
    await _require_seat_if_paid(pool, installation, session["github_login"])
    return installation


@admin_router.get("/admin/{org}/{repo}")
async def admin_page(org: str, repo: str, request: Request):
    installation = await _require_admin_installation(request, org, repo)
    pool = request.app.state.db_pool
    installation_id = installation["installation_id"]
    repo_full_name = f"{org}/{repo}"
    tokens = await list_api_tokens(pool, installation_id)
    members = await list_installation_members(pool, installation_id)
    included_seats = INCLUDED_SEATS.get(installation["plan"], DEFAULT_SEAT_LIMIT)
    seat_limit = included_seats + await get_extra_seats(pool, installation_id)
    health_targets = await list_health_check_targets(pool, installation_id, repo_full_name)
    health_target_limit = INCLUDED_HEALTH_CHECK_TARGETS.get(installation["plan"], DEFAULT_HEALTH_CHECK_TARGET_LIMIT)
    return {
        "installation": installation,
        "tokens": tokens,
        "members": members,
        "seat_limit": seat_limit,
        "health_targets": health_targets,
        "health_target_limit": health_target_limit,
        "public_status_url": f"/v1/health/{org}/{repo}",
        "branch_protection_disclosure": BRANCH_PROTECTION_DISCLOSURE,
    }


@admin_router.post("/admin/{org}/{repo}/members")
async def add_member(org: str, repo: str, request: Request, body: AddMemberRequest):
    installation = await _require_admin_installation(request, org, repo)
    session = await get_current_session(request)
    pool = request.app.state.db_pool
    installation_id = installation["installation_id"]

    included_seats = INCLUDED_SEATS.get(installation["plan"], DEFAULT_SEAT_LIMIT)
    seat_limit = included_seats + await get_extra_seats(pool, installation_id)
    if not await is_installation_member(pool, installation_id, body.github_login):
        if await count_installation_members(pool, installation_id) >= seat_limit:
            raise HTTPException(
                status_code=409,
                detail=(
                    f"seat limit reached ({seat_limit}) - additional seats need billing, "
                    "which isn't wired up yet. Remove someone or check back soon."
                ),
            )
        await add_installation_member(pool, installation_id, body.github_login, session["github_login"])
    return {"ok": True}


@admin_router.delete("/admin/{org}/{repo}/members/{github_login}")
async def remove_member(org: str, repo: str, github_login: str, request: Request):
    installation = await _require_admin_installation(request, org, repo)
    await remove_installation_member(request.app.state.db_pool, installation["installation_id"], github_login)
    return {"ok": True}


@admin_router.post("/admin/{org}/{repo}/tokens")
async def generate_token(org: str, repo: str, request: Request, body: GenerateTokenRequest):
    installation = await _require_admin_installation(request, org, repo)
    session = await get_current_session(request)
    pool = request.app.state.db_pool
    installation_id = installation["installation_id"]

    max_tokens = await get_max_tokens(pool, installation_id)
    if await count_active_tokens(pool, installation_id) >= max_tokens:
        raise HTTPException(status_code=409, detail=f"token limit reached ({max_tokens})")

    raw_token = secrets.token_urlsafe(32)
    token_hash = hashlib.sha256(raw_token.encode()).hexdigest()
    await create_api_token(pool, installation_id, token_hash, body.label, session["github_login"])
    token_id = (await list_api_tokens(pool, installation_id))[0]["id"]
    return {"token": raw_token, "id": token_id, "label": body.label}


@admin_router.delete("/admin/{org}/{repo}/tokens/{token_id}")
async def revoke_token(org: str, repo: str, token_id: int, request: Request):
    installation = await _require_admin_installation(request, org, repo)
    await revoke_api_token(request.app.state.db_pool, installation["installation_id"], token_id)
    return {"ok": True}


@admin_router.put("/admin/{org}/{repo}/webhook-url")
async def set_webhook_url_route(org: str, repo: str, request: Request, body: SetWebhookURLRequest):
    installation = await _require_admin_installation(request, org, repo)
    if body.webhook_url:
        try:
            validate_external_https_url(body.webhook_url)
        except UnsafeURLError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
    await set_webhook_url(
        request.app.state.db_pool,
        installation["installation_id"],
        body.webhook_url,
    )
    return {"ok": True}


@admin_router.post("/admin/{org}/{repo}/health-targets")
async def add_health_check_target_route(org: str, repo: str, request: Request, body: AddHealthCheckTargetRequest):
    installation = await _require_admin_installation(request, org, repo)
    try:
        validate_external_https_url(body.base_url)
    except UnsafeURLError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    pool = request.app.state.db_pool
    installation_id = installation["installation_id"]
    repo_full_name = f"{org}/{repo}"
    limit = INCLUDED_HEALTH_CHECK_TARGETS.get(installation["plan"], DEFAULT_HEALTH_CHECK_TARGET_LIMIT)
    if await count_health_check_targets(pool, installation_id, repo_full_name) >= limit:
        raise HTTPException(
            status_code=409,
            detail=f"health check target limit reached ({limit} for the {installation['plan']} plan)",
        )

    target_id = await add_health_check_target(
        pool, installation_id, repo_full_name, body.label, body.base_url, body.latency_threshold_ms
    )
    return {"id": target_id}


@admin_router.delete("/admin/{org}/{repo}/health-targets/{target_id}")
async def remove_health_check_target_route(org: str, repo: str, target_id: int, request: Request):
    installation = await _require_admin_installation(request, org, repo)
    await remove_health_check_target(
        request.app.state.db_pool, installation["installation_id"], f"{org}/{repo}", target_id
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
async def create_cli_token(request: Request, body: CreateCliTokenRequest):
    github_token = _bearer_github_token(request)
    installation_id = body.installation_id

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
    await create_api_token(pool, installation_id, token_hash, body.label, installation["account_login"])
    token_id = (await list_api_tokens(pool, installation_id))[0]["id"]
    return {"token": raw_token, "id": token_id, "label": body.label}
