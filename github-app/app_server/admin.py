import asyncio
import hashlib
import hmac
import json
import logging
import re
import secrets
import time
from datetime import datetime, timedelta, timezone

import asyncpg
import httpx
from fastapi import APIRouter, HTTPException, Request
from fastapi.encoders import jsonable_encoder
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

from app_server.affiliates import create_affiliate, list_affiliates_with_totals, mark_commissions_paid
from app_server.auth import encrypt_access_token, get_current_session, refresh_github_access_token
from app_server.config import get_settings
from app_server.github_auth import generate_app_jwt, get_installation_token, get_repo_permission_for_user
from app_server.github_pagination import fetch_paginated_github_collection
from app_server.http_client import get_github_api_client
from app_server.email_client import send_transactional_email
from app_server.email_queue import enqueue_transactional_email
from app_server.email_templates import deletion_otp_email
from scan_worker.pushover import send_pushover_alert
from app_server.db import (
    DEFAULT_HEALTH_CHECK_TARGET_LIMIT,
    DEFAULT_SEAT_LIMIT,
    INCLUDED_HEALTH_CHECK_TARGETS,
    INCLUDED_SEATS,
    add_health_check_target_within_limit,
    add_initial_installation_member_if_empty,
    add_installation_member_within_seat_limit,
    consume_deletion_otp_code,
    count_installation_members,
    create_api_token_within_limit,
    create_deletion_otp_code,
    delete_session,
    get_docs_repo_commit_settings,
    get_extra_seats,
    get_flash_review_count_this_month,
    get_github_user_email,
    get_installation,
    get_latest_evidence,
    get_llm_spend_this_month,
    get_max_tokens,
    get_public_status_enabled,
    is_installation_member,
    list_api_tokens,
    list_health_check_targets,
    list_health_check_targets_for_installation,
    list_installation_members,
    list_repos_for_installations,
    purge_installation_data,
    record_admin_action,
    record_installation_access,
    remove_health_check_target,
    remove_installation_member,
    revoke_api_token,
    set_docs_repo_commit_enabled,
    set_llm_suggestions_enabled,
    set_public_status_enabled,
    set_alert_email,
    set_pushover_user_key,
    set_webhook_url,
    update_session_tokens,
)
from app_server.llm_cost import EXTRA_SEAT_PRICE_USD, base_cap_for_plan, monthly_cap_for_installation
from app_server.paddle_client import PaddleAPIError
from app_server.paddle_client import create_discount as create_paddle_discount
from app_server.paddle_client import create_portal_session
from app_server.paddle_client import get_subscription as get_paddle_subscription
from app_server.paddle_client import update_subscription_items as update_paddle_subscription_items
from app_server.paddle_pricing import EXTRA_SEAT_PRICE_ID
from app_server.rate_limit import is_rate_limited
from app_server.redis_client import get_redis_client
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


def _looks_like_email(value: str) -> bool:
    # Deliberately not a regex. `^[^@\s]+@[^@\s]+\.[^@\s]+$` (the obvious
    # first attempt) is a real, exploitable polynomial-time ReDoS: CodeQL
    # flagged it, and a crafted ~100KB string ("!@!" + "!." * 50000) took
    # nearly 20 seconds to reject on this Python version, scaling
    # quadratically with input length - both [^@\s]+ groups can absorb '.'
    # characters, so a failing match forces backtracking across every
    # combination of '@' and '.' split points. Plain string operations
    # can't backtrack, so there's no equivalent attack surface. This is a
    # typo check, not full RFC 5321 validation - that job belongs to
    # Resend's own delivery attempt, not this endpoint.
    if not value or any(ch.isspace() for ch in value):
        return False
    local, at, domain = value.partition("@")
    if not local or at != "@" or "@" in domain:
        return False
    if not domain or domain.startswith(".") or domain.endswith("."):
        return False
    return "." in domain


class SetAlertEmailRequest(BaseModel):
    alert_email: str | None = None


# Pushover user/group keys are always exactly 30 characters from this
# charset (https://pushover.net/api#identifiers) - same "catch an obvious
# typo before it's saved" purpose as _looks_like_email above, not an
# attempt to verify the key is real (only Pushover's own API can do that,
# which is exactly what the /test route is for).
_PUSHOVER_KEY_PATTERN = re.compile(r"^[A-Za-z0-9]{30}$")


class SetPushoverUserKeyRequest(BaseModel):
    pushover_user_key: str | None = None


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


class SetLLMSuggestionsRequest(BaseModel):
    enabled: bool


class SetPublicStatusRequest(BaseModel):
    enabled: bool


class DeleteInstallationDataRequest(BaseModel):
    confirm: str = Field(min_length=1, max_length=200)
    otp_code: str = Field(min_length=6, max_length=6)


class AddMemberRequest(BaseModel):
    github_login: str = Field(min_length=1, max_length=39, pattern=_GITHUB_LOGIN_PATTERN)


# Paddle discount codes accept up to 32 alphanumeric characters.
class CreateAffiliateRequest(BaseModel):
    code: str = Field(min_length=1, max_length=32, pattern=r"^[A-Za-z0-9]+$")
    name: str = Field(min_length=1, max_length=200, pattern=_LABEL_PATTERN)


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
    return get_github_api_client()


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
    installations = fetch_paginated_github_collection(
        _github_http_client(),
        "/user/installations",
        headers={
            "Authorization": f"Bearer {github_token}",
            "Accept": "application/vnd.github+json",
        },
        collection_key="installations",
    )
    return {item["id"] for item in installations}


_ADMINISTERED_INSTALLATIONS_CACHE_TTL_SECONDS = 30


def _administered_installations_cache_key(github_token: str) -> str:
    # Never the raw token as a cache key - same discipline as API token
    # storage elsewhere in this file.
    return "administered-installations:" + hashlib.sha256(github_token.encode()).hexdigest()


async def _administered_installation_ids(github_token: str) -> set[int]:
    # This gates nearly every admin/dashboard route via
    # _require_admin_installation - a single dashboard page can fire
    # several requests, each previously making its own live GitHub API
    # round-trip here (latency, a hard GitHub-availability dependency for
    # the whole app, and burned user rate limit for no reason - the
    # answer barely changes install-to-install). A short cache absorbs
    # that fan-out; run off the event loop via asyncio.to_thread so one
    # slow GitHub round-trip (on a cache miss) still can't stall every
    # other in-flight request on this single-worker server.
    cache_key = _administered_installations_cache_key(github_token)
    try:
        cached = await asyncio.to_thread(get_redis_client().get, cache_key)
        if cached is not None:
            return {int(installation_id) for installation_id in json.loads(cached)}
    except Exception as exc:  # noqa: BLE001
        logger.warning("administered-installations cache read failed (%s); falling back to GitHub", exc)

    installation_ids = await asyncio.to_thread(_fetch_administered_installation_ids, github_token)

    try:
        await asyncio.to_thread(
            get_redis_client().set,
            cache_key,
            json.dumps(list(installation_ids)),
            _ADMINISTERED_INSTALLATIONS_CACHE_TTL_SECONDS,
        )
    except Exception as exc:  # noqa: BLE001
        logger.warning("administered-installations cache write failed (%s)", exc)

    return installation_ids


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


def _fetch_repo_permission_sync(installation_id: int, github_login: str, repo_full_name: str) -> str:
    settings = get_settings()
    app_jwt = generate_app_jwt(settings.github_app_id, settings.github_app_private_key)
    installation_token = get_installation_token(installation_id, app_jwt)
    return get_repo_permission_for_user(repo_full_name, github_login, installation_token)


async def _has_real_admin_permission(installation_id: int, github_login: str, repo_full_name: str) -> bool:
    """A stronger check than membership in the coarse `/user/installations`
    set (_administered_installation_ids draws from it) - GitHub documents
    that endpoint as listing every installation the caller has explicit
    *read*, write, or admin access to on any one repo it covers, so a
    read-only org member passes it too, same as a real admin. This queries
    GitHub's actual per-repo permission for the specific repo being
    administered instead, and is reserved for the two places the coarse
    set is too weak to trust on its own: claiming the first seat, and
    reaching the billing portal before any seat exists.

    Fails closed on any error (network, revoked token, GitHub outage) - an
    inability to verify real admin rights must never be treated as having
    them.
    """
    try:
        permission = await asyncio.to_thread(
            _fetch_repo_permission_sync, installation_id, github_login, repo_full_name
        )
    except Exception as exc:  # noqa: BLE001
        logger.warning(
            "real admin permission check failed for installation=%s login=%s repo=%s (%s)",
            installation_id, github_login, repo_full_name, exc,
        )
        return False
    return permission == "admin"


async def _require_seat_if_paid(pool, installation: dict, github_login: str, repo_full_name: str) -> None:
    """Paid installations gate on a seat, not just raw GitHub admin rights -
    GitHub's own "who can manage this installation" set is an org-side
    setting Aletheore doesn't control, and in many orgs is every Owner, so
    relying on it alone would let an unlimited number of people ride free
    on one purchase. Free plans skip this entirely - there's no seat
    revenue to protect there.

    If nobody has ever been seated yet (a paid installation from before
    seats existed, or the purchase webhook hasn't landed), the first
    *verified* GitHub admin to show up becomes seat one rather than every
    such customer being locked out of their own account - verified via
    _has_real_admin_permission, not just presence in the coarse
    administered-installations set, since that set alone would let any
    read-only org member with access to one repo the app is installed on
    claim the first seat and everything it unlocks (API tokens, team
    management).
    """
    if installation["plan"] == "free":
        return
    installation_id = installation["installation_id"]
    if await is_installation_member(pool, installation_id, github_login):
        return
    if not await _has_real_admin_permission(installation_id, github_login, repo_full_name):
        raise HTTPException(
            status_code=403,
            detail="you do not have admin access to this repository on GitHub",
        )
    if await add_initial_installation_member_if_empty(pool, installation_id, github_login, github_login):
        return
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
    # Every plan, not just paid seats - this is the record
    # purge_installation_data actually relies on to find PII to purge, since
    # installation_members only ever exists for paid seat holders.
    await record_installation_access(pool, installation_id, session["github_login"])
    return session, installation


async def _require_admin_installation(request: Request, org: str, repo: str) -> dict:
    session, installation = await _require_authorized_installation(request, org, repo)
    if installation["plan"] == "free":
        raise HTTPException(status_code=402, detail="this feature requires a paid plan")
    pool = request.app.state.db_pool
    await _require_seat_if_paid(pool, installation, session["github_login"], f"{org}/{repo}")
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
    public_status_enabled = await get_public_status_enabled(pool, installation_id, repo_full_name)
    return {
        "installation": installation,
        "tokens": tokens,
        "members": members,
        "seat_limit": seat_limit,
        "extra_seats": extra_seats,
        "health_targets": health_targets,
        "health_target_limit": health_target_limit,
        "public_status_url": f"/v1/health/{org}/{repo}",
        "public_status_enabled": public_status_enabled,
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
    allowed, inserted = await add_installation_member_within_seat_limit(
        pool, installation_id, body.github_login, session["github_login"], seat_limit
    )
    if not allowed:
        raise HTTPException(
            status_code=409,
            detail=(
                f"seat limit reached ({seat_limit}) - buy an extra seat in Settings "
                f"(${EXTRA_SEAT_PRICE_USD}/mo) or remove someone first."
            ),
        )
    if inserted:
        await record_admin_action(
            pool, installation_id, session["github_login"], "member_added",
            {"github_login": body.github_login},
        )
    return {"ok": True}


# buy_extra_seat and remove_extra_seat each do a read-then-write against
# Paddle's subscription (get_paddle_subscription, then a delta computed off
# what it returned, then update_subscription_items) with no lock around it.
# Two concurrent calls for the same installation - a double-click, or two
# admins both clicking around the same time - can both read the same
# starting quantity and each submit their own absolute (not relative) item
# list, so the second write silently clobbers the first rather than
# stacking: two "buy" clicks can net only +1 seat, with both requests
# returning 200. The app server runs as a single uvicorn worker (see
# Dockerfile.app-server's CMD, no --workers flag, and docker-compose.yml has
# no replicas set for app-server), so - unlike a multi-replica service - a
# plain in-process lock, keyed per installation, is sufficient to serialize
# this without needing a cross-process Postgres advisory lock held open
# across the Paddle network round-trip.
_SEAT_ADJUSTMENT_LOCKS: dict[int, asyncio.Lock] = {}


def _seat_adjustment_lock(installation_id: int) -> asyncio.Lock:
    lock = _SEAT_ADJUSTMENT_LOCKS.get(installation_id)
    if lock is None:
        lock = _SEAT_ADJUSTMENT_LOCKS[installation_id] = asyncio.Lock()
    return lock


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
        async with _seat_adjustment_lock(installation["installation_id"]):
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

    session = await get_current_session(request)
    await record_admin_action(
        request.app.state.db_pool, installation["installation_id"], session["github_login"],
        "extra_seat_purchase_requested",
    )
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
        async with _seat_adjustment_lock(installation["installation_id"]):
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

    session = await get_current_session(request)
    await record_admin_action(
        request.app.state.db_pool, installation["installation_id"], session["github_login"],
        "extra_seat_removal_requested",
    )
    return {"ok": True}


@admin_router.get("/admin/{org}/{repo}/billing-portal")
async def get_billing_portal_url(org: str, repo: str, request: Request):
    """Deliberately not behind _require_admin_installation's plan gate (see
    _require_authorized_installation) - a payment failure has already
    downgraded this installation to free by the time anyone would use this,
    and that's exactly who needs to reach it.

    _require_authorized_installation alone only proves membership in the
    coarse administered-installations set, which GitHub documents as
    including anyone with read access to a single repo the app covers -
    not enough to trust with a session that can view or change a payment
    method, or cancel the subscription outright. An already-seated member
    is trusted outright (paid access was already vetted when they were
    added); anyone else needs their real per-repo GitHub permission
    verified via _has_real_admin_permission, same bar as claiming the
    first seat.
    """
    session, installation = await _require_authorized_installation(request, org, repo)
    installation_id = installation["installation_id"]
    pool = request.app.state.db_pool
    if not await is_installation_member(pool, installation_id, session["github_login"]):
        if not await _has_real_admin_permission(installation_id, session["github_login"], f"{org}/{repo}"):
            raise HTTPException(
                status_code=403,
                detail="you do not have admin access to this repository on GitHub",
            )
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
    pool = request.app.state.db_pool
    await remove_installation_member(pool, installation["installation_id"], github_login)
    session = await get_current_session(request)
    await record_admin_action(
        pool, installation["installation_id"], session["github_login"], "member_removed",
        {"github_login": github_login},
    )
    return {"ok": True}


@admin_router.post("/admin/{org}/{repo}/tokens")
async def generate_token(org: str, repo: str, request: Request, body: GenerateTokenRequest):
    installation = await _require_admin_installation(request, org, repo)
    session = await get_current_session(request)
    pool = request.app.state.db_pool
    installation_id = installation["installation_id"]

    max_tokens = await get_max_tokens(pool, installation_id)
    raw_token = secrets.token_urlsafe(32)
    token_hash = hashlib.sha256(raw_token.encode()).hexdigest()
    token_id = await create_api_token_within_limit(
        pool, installation_id, token_hash, body.label, session["github_login"], max_tokens
    )
    if token_id is None:
        raise HTTPException(status_code=409, detail=f"token limit reached ({max_tokens})")
    # Label only - never the raw token or its hash.
    await record_admin_action(
        pool, installation_id, session["github_login"], "api_token_created",
        {"label": body.label, "token_id": token_id},
    )
    return {"token": raw_token, "id": token_id, "label": body.label}


@admin_router.delete("/admin/{org}/{repo}/tokens/{token_id}")
async def revoke_token(org: str, repo: str, token_id: int, request: Request):
    installation = await _require_admin_installation(request, org, repo)
    pool = request.app.state.db_pool
    await revoke_api_token(pool, installation["installation_id"], token_id)
    session = await get_current_session(request)
    await record_admin_action(
        pool, installation["installation_id"], session["github_login"], "api_token_revoked",
        {"token_id": token_id},
    )
    return {"ok": True}


@admin_router.put("/admin/{org}/{repo}/webhook-url")
async def set_webhook_url_route(org: str, repo: str, request: Request, body: SetWebhookURLRequest):
    installation = await _require_admin_installation(request, org, repo)
    if body.webhook_url:
        try:
            validate_external_https_url(body.webhook_url)
        except UnsafeURLError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
    pool = request.app.state.db_pool
    await set_webhook_url(pool, installation["installation_id"], body.webhook_url)
    session = await get_current_session(request)
    # Never the URL itself - it can carry a token/secret in its query string.
    await record_admin_action(
        pool, installation["installation_id"], session["github_login"], "webhook_url_changed",
        {"cleared": body.webhook_url is None},
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

    # validate_external_https_url only ran once, when the URL was saved
    # (set_webhook_url_route) - re-checking here, immediately before the
    # fetch, closes the DNS-rebinding window down to the gap between this
    # call and the actual request instead of "until someone edits the URL
    # again." Same reasoning and pattern as the health-check sweep (see
    # scan_worker/jobs.py's re-validation right before _endpoint_results).
    # An attacker who fully controls this webhook URL's domain could
    # otherwise register one that resolves to a public IP at save time,
    # pass validation, then repoint DNS at an internal service before
    # clicking "test."
    try:
        validate_external_https_url(webhook_url)
    except UnsafeURLError:
        raise HTTPException(status_code=400, detail="this webhook URL no longer resolves to a safe address") from None

    from scan_worker.slack import send_health_alert

    try:
        send_health_alert(
            webhook_url,
            {"text": f"*Aletheore*: test notification for `{org}/{repo}` - your alert webhook is configured correctly."},
        )
    except httpx.HTTPError:
        # Not the raw exception message - it's an SSRF oracle otherwise,
        # distinguishing "connection refused" from "timed out" from an
        # actual response body/status from whatever the URL resolved to.
        raise HTTPException(status_code=502, detail="could not reach that webhook URL") from None
    return {"ok": True}


@admin_router.put("/admin/{org}/{repo}/alert-email")
async def set_alert_email_route(org: str, repo: str, request: Request, body: SetAlertEmailRequest):
    installation = await _require_admin_installation(request, org, repo)
    if body.alert_email and not _looks_like_email(body.alert_email):
        raise HTTPException(status_code=400, detail="that doesn't look like a valid email address")
    pool = request.app.state.db_pool
    await set_alert_email(pool, installation["installation_id"], body.alert_email)
    session = await get_current_session(request)
    await record_admin_action(
        pool, installation["installation_id"], session["github_login"], "alert_email_changed",
        {"cleared": body.alert_email is None},
    )
    return {"ok": True}


@admin_router.post("/admin/{org}/{repo}/alert-email/test")
async def test_alert_email_route(org: str, repo: str, request: Request):
    # Same reasoning as test_webhook_url_route above - a customer has no
    # way to confirm the address is right short of waiting for a real
    # incident to fire one.
    installation = await _require_admin_installation(request, org, repo)
    alert_email = installation.get("alert_email")
    if not alert_email:
        raise HTTPException(status_code=400, detail="no alert email is configured yet")

    settings = get_settings()
    # dedupe_key includes wall-clock time, not just the installation - a
    # static key would silently no-op every click after the first, since
    # email_already_sent's record is permanent (see scan_worker/db.py).
    # The Slack/Teams test button doesn't have this problem (it sends
    # synchronously with no dedup at all); this reproduces the same
    # "always actually resends" behavior for email instead.
    enqueue_transactional_email(
        settings.redis_url,
        dedupe_key=f"health_alert_test:{installation['installation_id']}:{time.time()}",
        template_name="health_alert",
        template_arg=(
            f"*Aletheore*: test notification for `{org}/{repo}` - "
            "your alert email is configured correctly."
        ),
        to_email=alert_email,
        installation_id=installation["installation_id"],
    )
    return {"ok": True}


@admin_router.put("/admin/{org}/{repo}/pushover-user-key")
async def set_pushover_user_key_route(org: str, repo: str, request: Request, body: SetPushoverUserKeyRequest):
    installation = await _require_admin_installation(request, org, repo)
    if body.pushover_user_key and not _PUSHOVER_KEY_PATTERN.match(body.pushover_user_key):
        raise HTTPException(status_code=400, detail="that doesn't look like a valid Pushover user key")
    pool = request.app.state.db_pool
    await set_pushover_user_key(pool, installation["installation_id"], body.pushover_user_key)
    session = await get_current_session(request)
    await record_admin_action(
        pool, installation["installation_id"], session["github_login"], "pushover_user_key_changed",
        {"cleared": body.pushover_user_key is None},
    )
    return {"ok": True}


@admin_router.post("/admin/{org}/{repo}/pushover-user-key/test")
async def test_pushover_user_key_route(org: str, repo: str, request: Request):
    # Same reasoning as test_webhook_url_route/test_alert_email_route above
    # - a customer has no way to confirm the key is right short of waiting
    # for a real incident to fire one.
    installation = await _require_admin_installation(request, org, repo)
    pushover_user_key = installation.get("pushover_user_key")
    if not pushover_user_key:
        raise HTTPException(status_code=400, detail="no Pushover user key is configured yet")

    settings = get_settings()
    if not settings.pushover_api_token:
        # This installation's config is fine - Aletheore's own server-wide
        # Pushover Application token just isn't set yet. A generic 502 here
        # would look like the customer's key is wrong when it isn't.
        raise HTTPException(status_code=400, detail="push notifications aren't enabled on this server yet")

    try:
        send_pushover_alert(
            settings.pushover_api_token,
            pushover_user_key,
            {"text": f"*Aletheore*: test notification for `{org}/{repo}` - your Pushover key is configured correctly."},
        )
    except httpx.HTTPError:
        raise HTTPException(status_code=502, detail="could not send a Pushover notification to that key") from None
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
    pool = request.app.state.db_pool
    await set_docs_repo_commit_enabled(pool, installation["installation_id"], f"{org}/{repo}", body.enabled)
    session = await get_current_session(request)
    await record_admin_action(
        pool, installation["installation_id"], session["github_login"], "docs_repo_commit_setting_changed",
        {"repo_full_name": f"{org}/{repo}", "enabled": body.enabled},
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
    target_id = await add_health_check_target_within_limit(
        pool, installation_id, repo_full_name, body.label, body.base_url, body.latency_threshold_ms, limit
    )
    if target_id is None:
        raise HTTPException(
            status_code=409,
            detail=f"health check target limit reached ({limit} for the {installation['plan']} plan)",
        )
    session = await get_current_session(request)
    await record_admin_action(
        pool, installation_id, session["github_login"], "health_check_target_added",
        {"repo_full_name": repo_full_name, "label": body.label, "target_id": target_id},
    )
    return {"id": target_id}


@admin_router.delete("/admin/{org}/{repo}/health-targets/{target_id}")
async def remove_health_check_target_route(org: str, repo: str, target_id: int, request: Request):
    installation = await _require_admin_installation(request, org, repo)
    pool = request.app.state.db_pool
    await remove_health_check_target(pool, installation["installation_id"], f"{org}/{repo}", target_id)
    session = await get_current_session(request)
    await record_admin_action(
        pool, installation["installation_id"], session["github_login"], "health_check_target_removed",
        {"repo_full_name": f"{org}/{repo}", "target_id": target_id},
    )
    return {"ok": True}


@admin_router.put("/admin/{org}/{repo}/llm-suggestions")
async def set_llm_suggestions_route(
    org: str, repo: str, request: Request, body: SetLLMSuggestionsRequest
):
    """Opt the installation in or out of the non-evidence-backed suggestion
    section on managed audits.

    Uses the admin gate (paid plan + seat) rather than the looser authorized
    gate, because managed audits are a paid feature - an installation with no
    audits to configure has nothing to set here.
    """
    installation = await _require_admin_installation(request, org, repo)
    pool = request.app.state.db_pool
    await set_llm_suggestions_enabled(pool, installation["installation_id"], body.enabled)
    session = await get_current_session(request)
    await record_admin_action(
        pool, installation["installation_id"], session["github_login"], "llm_suggestions_setting_changed",
        {"enabled": body.enabled},
    )
    return {"ok": True, "llm_suggestions_enabled": body.enabled}


@admin_router.put("/admin/{org}/{repo}/public-status")
async def set_public_status_route(org: str, repo: str, request: Request, body: SetPublicStatusRequest):
    """Opt-in for the unauthenticated /v1/health/{org}/{repo} status API
    (dashboard.py) - endpoint paths, reachability, and latency derived
    from this repo are exposed to anyone who knows the org/repo, with no
    other access control. Off by default (migration 043); this is the
    only way to turn it on."""
    installation = await _require_admin_installation(request, org, repo)
    pool = request.app.state.db_pool
    await set_public_status_enabled(pool, installation["installation_id"], f"{org}/{repo}", body.enabled)
    session = await get_current_session(request)
    await record_admin_action(
        pool, installation["installation_id"], session["github_login"], "public_status_setting_changed",
        {"repo_full_name": f"{org}/{repo}", "enabled": body.enabled},
    )
    return {"ok": True, "public_status_enabled": body.enabled}


@admin_router.get("/admin/{org}/{repo}/export-data")
async def export_data(org: str, repo: str, request: Request):
    """Self-serve export of what the hosted service holds for this
    installation, as a single downloadable JSON file.

    Gated on _require_authorized_installation, same as deletion-preview and
    delete-all-data: no plan or seat gate. Exporting your own data isn't a
    paid feature to unlock, and a customer whose payment failed still owns
    what's already here.

    Deliberately excludes anything a leaked export would turn into a
    working credential: API tokens are listed by label/id only, never the
    token or its hash (list_api_tokens never returns either); the webhook
    URL is omitted entirely, since Slack-style webhook URLs embed a secret
    in the path itself.
    """
    session, installation = await _require_authorized_installation(request, org, repo)
    pool = request.app.state.db_pool
    installation_id = installation["installation_id"]

    repos = await list_repos_for_installations(pool, [installation_id])
    repo_full_names = [row["repo_full_name"] for row in repos]
    findings_by_repo = {}
    for full_name in repo_full_names:
        evidence = await get_latest_evidence(pool, installation_id, full_name)
        if evidence is not None:
            findings_by_repo[full_name] = evidence

    extra_seats = await get_extra_seats(pool, installation_id)
    export = {
        "exported_at": datetime.now(timezone.utc).isoformat(),
        "account_login": installation["account_login"],
        "plan": installation["plan"],
        "connected_repos": repo_full_names,
        "latest_findings_by_repo": findings_by_repo,
        "members": await list_installation_members(pool, installation_id),
        "api_tokens": await list_api_tokens(pool, installation_id),
        "health_check_targets": await list_health_check_targets_for_installation(pool, installation_id),
        "extra_seats": extra_seats,
        "llm_spend_month_to_date": await get_llm_spend_this_month(pool, installation_id),
        "flash_reviews_month_to_date": await get_flash_review_count_this_month(pool, installation_id),
    }

    await record_admin_action(pool, installation_id, session["github_login"], "data_exported")

    filename = f"aletheore-export-{installation['account_login']}-{datetime.now(timezone.utc).strftime('%Y%m%d')}.json"
    return JSONResponse(
        content=jsonable_encoder(export),
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


@admin_router.get("/admin/{org}/{repo}/deletion-preview")
async def deletion_preview(org: str, repo: str, request: Request):
    """What a purge would actually destroy, so the confirmation dialog can
    say it out loud. Deletion is installation-wide but every admin route is
    repo-scoped, so a customer standing on acme/api's settings page is one
    click from wiping acme/web too - naming the other repos is the only
    honest way to present that.
    """
    _session, installation = await _require_authorized_installation(request, org, repo)
    pool = request.app.state.db_pool
    installation_id = installation["installation_id"]
    repos = await list_repos_for_installations(pool, [installation_id])
    return {
        "account_login": installation["account_login"],
        "repos": [row["repo_full_name"] for row in repos],
        "member_count": await count_installation_members(pool, installation_id),
    }


DELETION_OTP_RATE_LIMIT = 5
DELETION_OTP_RATE_LIMIT_WINDOW_SECONDS = 3600
DELETION_OTP_ATTEMPT_LIMIT = 10
DELETION_OTP_ATTEMPT_WINDOW_SECONDS = 3600
DELETION_OTP_VALIDITY_MINUTES = 10


@admin_router.post("/admin/{org}/{repo}/delete-all-data/request-otp")
async def request_deletion_otp(org: str, repo: str, request: Request):
    """Emails a one-time code required to actually run delete-all-data.

    The typed account-login confirmation on delete-all-data defends against
    an accidental click - the name is right there on the page. It proves
    nothing about who's asking, since anyone holding a stolen session
    cookie can read that name and type it back. This closes that gap: the
    code goes to the acting session's own captured email (not necessarily
    the account owner's - a teammate with their own seat can delete too),
    so completing a delete requires proving control of that inbox right
    now, not just possession of a session.
    """
    session, installation = await _require_authorized_installation(request, org, repo)
    pool = request.app.state.db_pool
    installation_id = installation["installation_id"]

    settings = get_settings()
    try:
        rate_limited = is_rate_limited(
            get_redis_client(),
            f"ratelimit:deletion-otp:{installation_id}",
            DELETION_OTP_RATE_LIMIT,
            DELETION_OTP_RATE_LIMIT_WINDOW_SECONDS,
        )
    except Exception as exc:  # noqa: BLE001
        logger.warning("deletion OTP rate limit check failed (%s); allowing request", exc)
        rate_limited = False
    if rate_limited:
        raise HTTPException(status_code=429, detail="too many code requests - try again later")

    to_email = await get_github_user_email(pool, session["github_login"])
    if not to_email:
        raise HTTPException(
            status_code=400,
            detail="no verified email on file for your GitHub account - contact support@aletheore.com",
        )

    code = f"{secrets.randbelow(1_000_000):06d}"
    code_hash = hashlib.sha256(code.encode()).hexdigest()
    expires_at = datetime.now(timezone.utc) + timedelta(minutes=DELETION_OTP_VALIDITY_MINUTES)
    await create_deletion_otp_code(pool, installation_id, session["github_login"], code_hash, expires_at)

    if settings.resend_api_key:
        message = deletion_otp_email(installation["account_login"], code)
        await asyncio.to_thread(
            send_transactional_email,
            settings.resend_api_key,
            settings.email_from_address,
            settings.email_reply_to_address,
            to_email,
            message["subject"],
            message["html"],
            message["text"],
        )
    else:
        # No email provider configured (local dev) - log it instead of
        # silently succeeding with a code nobody can ever receive.
        logger.warning("RESEND_API_KEY not configured, deletion OTP not sent: %s", code)

    masked = to_email[0] + "***" + to_email[to_email.index("@"):] if "@" in to_email else "***"
    return {"ok": True, "sent_to": masked}


@admin_router.post("/admin/{org}/{repo}/delete-all-data")
async def delete_all_data(
    org: str, repo: str, request: Request, body: DeleteInstallationDataRequest
):
    """Self-serve erasure for everything the hosted service holds.

    Gated on _require_authorized_installation, not _require_admin_installation:
    no plan gate and no seat gate. Same reasoning as the billing portal above -
    a customer on the free plan, or one whose card just failed and got
    downgraded, is precisely the person who must still be able to delete
    their data. A 402 on this route would be indefensible.
    """
    session, installation = await _require_authorized_installation(request, org, repo)
    pool = request.app.state.db_pool
    installation_id = installation["installation_id"]

    # The typed confirmation is the account login, not the repo name: the
    # blast radius is the whole installation, and making someone type the
    # repo they happen to be looking at would misrepresent that.
    if body.confirm.strip() != installation["account_login"]:
        raise HTTPException(
            status_code=400,
            detail=f"type {installation['account_login']} exactly to confirm deletion",
        )

    try:
        attempt_limited = is_rate_limited(
            get_redis_client(),
            f"ratelimit:deletion-otp-attempt:{installation_id}",
            DELETION_OTP_ATTEMPT_LIMIT,
            DELETION_OTP_ATTEMPT_WINDOW_SECONDS,
        )
    except Exception as exc:  # noqa: BLE001
        logger.warning("deletion OTP attempt rate limit check failed (%s); allowing request", exc)
        attempt_limited = False
    if attempt_limited:
        raise HTTPException(status_code=429, detail="too many incorrect codes - try again later")

    code_hash = hashlib.sha256(body.otp_code.strip().encode()).hexdigest()
    if not await consume_deletion_otp_code(pool, installation_id, code_hash):
        raise HTTPException(
            status_code=400,
            detail="that code is invalid, expired, or already used - request a new one",
        )

    result = await purge_installation_data(pool, installation_id, session["github_login"])
    if result is None:
        raise HTTPException(status_code=404, detail="installation not found")

    # purge_installation_data is SQL-only - app-server has no filesystem
    # access to the persistent-checkout volume scan-worker owns (see
    # scan_worker.jobs._ensure_persistent_checkout), so the on-disk purge
    # runs there instead, same as the uninstall webhook.
    from rq import Queue

    Queue("scans", connection=get_redis_client()).enqueue(
        "scan_worker.jobs.purge_persistent_checkouts_job",
        job_timeout=120,
        installation_id=installation_id,
    )

    logger.info(
        "purged installation %s (%s) on request of %s: %s repos, %s users",
        result["installation_id"],
        result["account_login"],
        session["github_login"],
        result["repos_deleted"],
        result["users_purged"],
    )
    return {"ok": True, **result}


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
    raw_token = secrets.token_urlsafe(32)
    token_hash = hashlib.sha256(raw_token.encode()).hexdigest()
    token_id = await create_api_token_within_limit(
        pool, installation_id, token_hash, body.label, installation["account_login"], max_tokens
    )
    if token_id is None:
        raise HTTPException(status_code=409, detail=f"token limit reached ({max_tokens})")
    return {"token": raw_token, "id": token_id, "label": body.label}


# ---------------------------------------------------------------------------
# Affiliate program - internal admin only, not scoped to a customer
# installation. Same optional/404-if-unset bearer-token pattern as
# /v1/internal/queue-stats in metrics.py, gated on its own dedicated
# affiliate_admin_token rather than reusing internal_metrics_token: this
# token can create real Paddle discount codes and see revenue, a different
# privilege level than read-only queue stats. See
# docs/superpowers/specs/2026-08-10-aletheore-affiliate-program-design.md.
# ---------------------------------------------------------------------------


def _require_affiliate_admin_token(request: Request) -> None:
    settings = get_settings()
    if not settings.affiliate_admin_token:
        raise HTTPException(status_code=404, detail="not found")
    auth_header = request.headers.get("Authorization", "")
    if not hmac.compare_digest(
        auth_header.encode(), f"Bearer {settings.affiliate_admin_token}".encode()
    ):
        raise HTTPException(status_code=401, detail="missing or invalid token")


@admin_router.post("/admin/affiliates")
async def create_affiliate_route(request: Request, body: CreateAffiliateRequest):
    _require_affiliate_admin_token(request)
    settings = get_settings()
    try:
        # Off the event loop: create_paddle_discount is a blocking httpx
        # call, same reasoning as every other synchronous paddle_client
        # call in this file (see _adjust_extra_seat_sync and
        # get_billing_portal_url above).
        discount = await asyncio.to_thread(
            create_paddle_discount, settings.paddle_api_key, body.code, f"Affiliate: {body.name}"
        )
    except PaddleAPIError as exc:
        logger.error("affiliate discount creation failed for code %s: %s", body.code, exc)
        raise HTTPException(
            status_code=502, detail="Could not create the Paddle discount code right now."
        ) from exc

    pool = request.app.state.db_pool
    try:
        affiliate = await create_affiliate(pool, body.code, discount["id"], body.name)
    except asyncpg.UniqueViolationError as exc:
        raise HTTPException(status_code=409, detail="an affiliate with that code already exists") from exc
    return jsonable_encoder(affiliate)


@admin_router.get("/admin/affiliates")
async def list_affiliates_route(request: Request):
    _require_affiliate_admin_token(request)
    pool = request.app.state.db_pool
    affiliates = await list_affiliates_with_totals(pool)
    return {"affiliates": jsonable_encoder(affiliates)}


@admin_router.post("/admin/affiliates/{affiliate_id}/mark-paid")
async def mark_affiliate_paid_route(affiliate_id: int, request: Request):
    _require_affiliate_admin_token(request)
    pool = request.app.state.db_pool
    marked_count = await mark_commissions_paid(pool, affiliate_id)
    return {"marked_paid_count": marked_count}
