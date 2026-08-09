import asyncio
import hashlib
import logging
import secrets

import httpx
from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field

from app_server.auth import encrypt_access_token, get_current_session, refresh_github_access_token
from app_server.config import get_settings
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
    delete_session,
    get_docs_repo_commit_settings,
    get_extra_seats,
    get_flash_review_count_this_month,
    get_installation,
    get_llm_spend_this_month,
    get_max_tokens,
    is_installation_member,
    list_api_tokens,
    list_health_check_targets,
    list_installation_members,
    remove_health_check_target,
    remove_installation_member,
    revoke_api_token,
    set_docs_repo_commit_enabled,
    set_webhook_url,
    update_session_tokens,
)
from app_server.llm_cost import base_cap_for_plan, monthly_cap_for_installation
from app_server.paddle_client import PaddleAPIError
from app_server.paddle_client import create_portal_session
from app_server.paddle_client import get_subscription as get_paddle_subscription
from app_server.paddle_client import update_subscription_items as update_paddle_subscription_items
from app_server.paddle_pricing import EXTRA_SEAT_PRICE_ID
from app_server.url_validation import UnsafeURLError, validate_external_https_url

admin_router = APIRouter()
logger = logging.getLogger(__name__)

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


class SetDocsRepoCommitRequest(BaseModel):
    enabled: bool


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
    "required status check in this repository's branch protection settings. Managed audits "
    "(paid plans) also post an \"Aletheore Audit Certificate\" check with a cryptographically "
    "signed (Ed25519), independently verifiable record of the audit - mark that as required "
    "too to block merges lacking a valid, freshly-signed audit. That check attests provenance "
    "and integrity, not quality: it is green whenever an audit ran, and the audit's own "
    "findings and Citation Verification section are what report whether anything needs "
    "attention."
)


def _github_http_client() -> httpx.Client:
    return httpx.Client(base_url="https://api.github.com")


async def _repo_installation_id(pool, org: str, repo: str) -> int:
    # A GitHub App installation covers exactly one account (org or user) -
    # a repo's owner in the URL is always that same account - so this
    # resolves without ever touching repo_history, which used to be the
    # only lookup here and only has a row once a repo has been scanned at
    # least once. That made every route built on this 404 for a repo
    # that's connected but genuinely never scanned yet (a real gap: the
    # Docs page's own "nothing scanned yet" empty-state response could
    # never be reached, since this raised first). repo_history stays as
    # a fallback for account_login drift (e.g. a GitHub account rename
    # landing after this row was written), not the primary path anymore.
    row = await pool.fetchrow(
        "SELECT installation_id FROM installations WHERE account_login = $1",
        org,
    )
    if row is not None:
        return row["installation_id"]
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


def _fetch_administered_installation_ids(github_token: str) -> set[int]:
    response = _github_http_client().get(
        "/user/installations",
        headers={
            "Authorization": f"Bearer {github_token}",
            "Accept": "application/vnd.github+json",
        },
    )
    response.raise_for_status()
    return {item["id"] for item in response.json().get("installations", [])}


async def _administered_installation_ids(github_token: str) -> set[int]:
    # This gates nearly every admin/dashboard route via
    # _require_admin_installation - run off the event loop via
    # asyncio.to_thread so one slow GitHub API round-trip can't stall
    # every other in-flight request on this single-worker server.
    return await asyncio.to_thread(_fetch_administered_installation_ids, github_token)


async def _administered_installation_ids_or_401(github_token: str) -> set[int]:
    """Same as _administered_installation_ids, but for JSON API callers that
    just want a clean 401/502 rather than handling httpx.HTTPStatusError
    themselves - unlike /subscribe (frontend.py), which needs the raw
    exception to redirect to sign-in and clear the dead session cookie, a
    JSON endpoint's caller is the frontend's own apiGet() helper, which
    already redirects to sign-in on any 401 response. Before this existed,
    a revoked/expired GitHub token here surfaced as an unhandled 500 whose
    non-JSON body then crashed the page trying to parse it as JSON.
    """
    try:
        return await _administered_installation_ids(github_token)
    except httpx.HTTPStatusError as exc:
        if exc.response.status_code in (401, 403):
            raise HTTPException(status_code=401, detail="GitHub session expired - please sign in again") from exc
        raise HTTPException(status_code=502, detail="GitHub API unavailable") from exc


async def _try_refresh_session_token(pool, session: dict) -> str | None:
    """Attempts to renew a session's GitHub access token via its stored
    refresh_token. Returns the new access token on success, None if
    there's no refresh_token on file or GitHub rejects it - callers treat
    None as "refresh isn't possible," not as an error to bubble up.
    """
    refresh_token = session.get("github_refresh_token")
    if not refresh_token:
        return None

    settings = get_settings()
    try:
        new_access_token, new_refresh_token = await asyncio.to_thread(
            refresh_github_access_token,
            refresh_token,
            settings.github_client_id,
            settings.github_client_secret,
        )
    except Exception:
        logger.warning(
            "GitHub token refresh failed for session %s", session["id"], exc_info=True
        )
        return None

    await update_session_tokens(
        pool,
        session["id"],
        encrypt_access_token(new_access_token, settings.session_secret),
        # GitHub rotates the refresh token on every use but doesn't always
        # send a new one back - keep the still-valid old one on file
        # rather than stranding the session with none at all.
        encrypt_access_token(new_refresh_token or refresh_token, settings.session_secret),
    )
    return new_access_token


async def _administered_installation_ids_for_session_or_401(pool, session: dict) -> set[int]:
    """Same idea as _administered_installation_ids_or_401, but for
    session-cookie callers specifically: a 401/403 from GitHub here means
    the session's stored access token is dead, not necessarily that the
    user needs to fully re-authenticate. If a refresh_token is on file,
    this transparently renews it and retries once before giving up.

    The session cookie itself has its own 30-day TTL and no way to know
    the GitHub token it wraps expired early (get_current_session only
    checks the cookie's own signature/TTL) - if refresh isn't possible or
    also fails, the session is deleted outright so the cookie stops
    looking valid, and the frontend's redirect to /auth/logout on a 401
    actually resolves something instead of looping forever.
    """
    try:
        return await _administered_installation_ids(session["github_access_token"])
    except httpx.HTTPStatusError as exc:
        if exc.response.status_code not in (401, 403):
            raise HTTPException(status_code=502, detail="GitHub API unavailable") from exc

    refreshed_token = await _try_refresh_session_token(pool, session)
    if refreshed_token is not None:
        try:
            return await _administered_installation_ids(refreshed_token)
        except httpx.HTTPStatusError as exc:
            if exc.response.status_code not in (401, 403):
                raise HTTPException(status_code=502, detail="GitHub API unavailable") from exc

    await delete_session(pool, session["id"])
    raise HTTPException(status_code=401, detail="GitHub session expired - please sign in again")


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


async def _require_authorized_installation(request: Request, org: str, repo: str) -> tuple[dict, dict]:
    """Session + "do you administer this installation" only - no plan or
    seat gate. Returns (session, installation).

    Split out of _require_admin_installation for the billing portal: a
    customer whose card just failed is exactly the person who needs to
    reach it, and by then Paddle's own webhook has already downgraded the
    installation to free - gating the portal on "not free" would lock out
    the one person who needs to fix it.
    """
    session = await get_current_session(request)
    if session is None:
        raise HTTPException(status_code=401, detail="login required")

    pool = request.app.state.db_pool
    installation_id = await _repo_installation_id(pool, org, repo)
    administered_ids = await _administered_installation_ids_for_session_or_401(pool, session)
    if installation_id not in administered_ids:
        raise HTTPException(status_code=403, detail="you do not administer this installation")

    installation = await get_installation(pool, installation_id)
    if installation is None:
        raise HTTPException(status_code=404, detail="installation not found")
    return session, installation


async def _require_admin_installation(request: Request, org: str, repo: str) -> dict:
    session, installation = await _require_authorized_installation(request, org, repo)
    if installation["plan"] == "free":
        raise HTTPException(status_code=402, detail="this feature requires a paid plan")
    pool = request.app.state.db_pool
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
    extra_seats = await get_extra_seats(pool, installation_id)
    seat_limit = included_seats + extra_seats
    health_targets = await list_health_check_targets(pool, installation_id, repo_full_name)
    health_target_limit = INCLUDED_HEALTH_CHECK_TARGETS.get(installation["plan"], DEFAULT_HEALTH_CHECK_TARGET_LIMIT)
    llm_spend_month_to_date = await get_llm_spend_this_month(pool, installation_id)
    flash_reviews_month_to_date = await get_flash_review_count_this_month(pool, installation_id)
    llm_spend_cap = monthly_cap_for_installation(base_cap_for_plan(installation["plan"]), extra_seats)
    return {
        "installation": installation,
        "tokens": tokens,
        "members": members,
        "seat_limit": seat_limit,
        "extra_seats": extra_seats,
        "health_targets": health_targets,
        "health_target_limit": health_target_limit,
        "public_status_url": f"/v1/health/{org}/{repo}",
        "branch_protection_disclosure": BRANCH_PROTECTION_DISCLOSURE,
        # llm_spend and flash_review_monthly_count are already tracked
        # internally for the hard spend cap (scan_worker/jobs.py) - this is
        # the first place a customer actually sees what their AI review
        # usage is costing/producing, previously invisible to them.
        "llm_spend_month_to_date": llm_spend_month_to_date,
        "llm_spend_cap": llm_spend_cap,
        "flash_reviews_month_to_date": flash_reviews_month_to_date,
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
                    f"seat limit reached ({seat_limit}) - buy an extra seat in Settings "
                    "($3.99/mo) or remove someone first."
                ),
            )
        await add_installation_member(pool, installation_id, body.github_login, session["github_login"])
    return {"ok": True}


def _build_updated_seat_items(subscription_items: list[dict], delta: int) -> list[dict] | None:
    # Paddle requires the complete item list on every subscription update -
    # this rebuilds it with the extra-seat item's quantity adjusted by
    # delta (adding the item at quantity 1 if it doesn't exist yet).
    # Returns None if delta would take the seat item below zero.
    items = []
    seat_item_found = False
    for item in subscription_items:
        price_id = item["price"]["id"]
        quantity = item["quantity"]
        if price_id == EXTRA_SEAT_PRICE_ID:
            seat_item_found = True
            quantity += delta
            if quantity < 0:
                return None
            if quantity == 0:
                continue
        items.append({"price_id": price_id, "quantity": quantity})
    if not seat_item_found:
        if delta <= 0:
            return None
        items.append({"price_id": EXTRA_SEAT_PRICE_ID, "quantity": delta})
    return items


def _adjust_extra_seat_sync(api_key: str | None, subscription_id: str, delta: int) -> list[dict] | None:
    """Fetches the current subscription, computes its item list with the
    extra-seat quantity adjusted by delta, and (if that's still a valid
    quantity) pushes the update to Paddle. Two real network calls - run off
    the event loop via asyncio.to_thread by the caller, same reasoning as
    _administered_installation_ids: this gates a billing action behind a
    single-worker server, and a slow Paddle round-trip would otherwise
    freeze every other in-flight request too.
    """
    subscription = get_paddle_subscription(api_key, subscription_id)
    items = _build_updated_seat_items(subscription.get("items", []), delta=delta)
    if items is not None:
        update_paddle_subscription_items(api_key, subscription_id, items, "prorated_immediately")
    return items


@admin_router.post("/admin/{org}/{repo}/seats/buy")
async def buy_extra_seat(org: str, repo: str, request: Request):
    installation = await _require_admin_installation(request, org, repo)
    subscription_id = installation.get("paddle_subscription_id")
    if not subscription_id:
        raise HTTPException(status_code=400, detail="no active subscription to add a seat to")

    settings = get_settings()
    try:
        await asyncio.to_thread(
            _adjust_extra_seat_sync, settings.paddle_api_key, subscription_id, 1
        )
    except PaddleAPIError as exc:
        # exc's message includes the raw Paddle response (URL, status code,
        # docs link) - useful in a log, not something to hand an end user
        # verbatim. Logged with the installation for whoever's debugging;
        # the customer gets a message that tells them what to do next.
        logger.error(
            "seat purchase failed for installation %s (subscription %s): %s",
            installation["installation_id"],
            subscription_id,
            exc,
        )
        raise HTTPException(
            status_code=502,
            detail="Could not update billing right now - please try again, or contact support if this keeps happening.",
        ) from exc

    # extra_seats itself is reconciled from the resulting subscription.updated
    # webhook, not set optimistically here - same pattern installations.plan
    # already follows for the base subscription price.
    return {"ok": True}


@admin_router.post("/admin/{org}/{repo}/seats/remove")
async def remove_extra_seat(org: str, repo: str, request: Request):
    installation = await _require_admin_installation(request, org, repo)
    subscription_id = installation.get("paddle_subscription_id")
    if not subscription_id:
        raise HTTPException(status_code=400, detail="no active subscription to remove a seat from")

    settings = get_settings()
    try:
        items = await asyncio.to_thread(
            _adjust_extra_seat_sync, settings.paddle_api_key, subscription_id, -1
        )
        if items is None:
            raise HTTPException(status_code=409, detail="no extra seats to remove")
    except PaddleAPIError as exc:
        logger.error(
            "seat removal failed for installation %s (subscription %s): %s",
            installation["installation_id"],
            subscription_id,
            exc,
        )
        raise HTTPException(
            status_code=502,
            detail="Could not update billing right now - please try again, or contact support if this keeps happening.",
        ) from exc

    return {"ok": True}


@admin_router.get("/admin/{org}/{repo}/billing-portal")
async def get_billing_portal_url(org: str, repo: str, request: Request):
    """Deliberately not behind _require_admin_installation's plan gate (see
    _require_authorized_installation) - a payment failure has already
    downgraded this installation to free by the time anyone would use this,
    and that's exactly who needs to reach it."""
    _, installation = await _require_authorized_installation(request, org, repo)
    customer_id = installation.get("paddle_customer_id")
    if not customer_id:
        raise HTTPException(
            status_code=400, detail="no billing account on file yet - subscribe first to set one up"
        )
    subscription_id = installation.get("paddle_subscription_id")
    subscription_ids = [subscription_id] if subscription_id else None

    settings = get_settings()
    try:
        session_data = await asyncio.to_thread(
            create_portal_session, settings.paddle_api_key, customer_id, subscription_ids
        )
    except PaddleAPIError as exc:
        logger.error(
            "billing portal session failed for installation %s (customer %s): %s",
            installation["installation_id"],
            customer_id,
            exc,
        )
        raise HTTPException(
            status_code=502,
            detail="Could not open the billing portal right now - please try again, or contact support if this keeps happening.",
        ) from exc

    urls = session_data.get("urls", {})
    # Prefer a subscription-scoped deep link (lets the customer update their
    # payment method for this subscription directly) over the general
    # account overview, when Paddle returned one - it only will if
    # subscription_ids was non-empty above. Confirmed against a real portal
    # session response: each entry has update_subscription_payment_method
    # directly on it, not nested under a further "urls" key.
    subscription_urls = urls.get("subscriptions") or []
    url = subscription_urls[0]["update_subscription_payment_method"] if subscription_urls else None
    if url is None:
        url = urls.get("general", {}).get("overview")
    if url is None:
        raise HTTPException(status_code=502, detail="Paddle did not return a portal URL")
    return {"url": url}


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


@admin_router.post("/admin/{org}/{repo}/webhook-url/test")
async def test_webhook_url_route(org: str, repo: str, request: Request):
    # Without this, a customer has no way to confirm a webhook URL is
    # correctly configured short of waiting for a real finding or
    # incident to fire one - by then a typo or a dead/rotated URL has
    # already meant silently missed alerts.
    installation = await _require_admin_installation(request, org, repo)
    webhook_url = installation.get("webhook_url")
    if not webhook_url:
        raise HTTPException(status_code=400, detail="no alert webhook is configured yet")

    from scan_worker.slack import send_health_alert

    try:
        send_health_alert(
            webhook_url,
            {"text": f"*Aletheore*: test notification for `{org}/{repo}` - your alert webhook is configured correctly."},
        )
    except httpx.HTTPError as exc:
        raise HTTPException(status_code=502, detail=f"could not reach webhook: {exc}") from exc
    return {"ok": True}


@admin_router.get("/admin/{org}/{repo}/docs-repo-commit")
async def get_docs_repo_commit_route(org: str, repo: str, request: Request):
    installation = await _require_admin_installation(request, org, repo)
    settings = await get_docs_repo_commit_settings(
        request.app.state.db_pool, installation["installation_id"], f"{org}/{repo}"
    )
    return {
        "enabled": settings["enabled"] if settings is not None else False,
        "pr_number": settings["pr_number"] if settings is not None else None,
    }


@admin_router.put("/admin/{org}/{repo}/docs-repo-commit")
async def set_docs_repo_commit_route(org: str, repo: str, request: Request, body: SetDocsRepoCommitRequest):
    # Writes into the customer's own repository (a branch + PR under
    # .aletheore/docs/), unlike every other Docs surface which only reads
    # evidence - opt-in and reversible at any time, never defaulted on.
    installation = await _require_admin_installation(request, org, repo)
    await set_docs_repo_commit_enabled(
        request.app.state.db_pool, installation["installation_id"], f"{org}/{repo}", body.enabled
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
    administered_ids = await _administered_installation_ids_or_401(github_token)
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

    administered_ids = await _administered_installation_ids_or_401(github_token)
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
