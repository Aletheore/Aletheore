import asyncio
import base64
import hmac
import logging
import secrets
from datetime import datetime, timedelta, timezone

import httpx
from cryptography.fernet import Fernet
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.kdf.hkdf import HKDF
from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import RedirectResponse
from itsdangerous import BadSignature, URLSafeTimedSerializer

from app_server.config import get_settings
from app_server.db import (
    create_session,
    delete_session,
    get_session,
    upsert_github_user_email,
    upsert_installation,
)
from app_server.email_queue import enqueue_transactional_email
from app_server.github_auth import generate_app_jwt, get_installation_details
from app_server.paddle_ip_allowlist import client_ip_from_forwarded_for
from app_server.rate_limit import is_rate_limited

SESSION_COOKIE_NAME = "session"
SESSION_TTL = timedelta(days=30)
OAUTH_STATE_COOKIE_NAME = "oauth_state"
OAUTH_STATE_TTL = timedelta(minutes=10)
NEXT_COOKIE_NAME = "aletheore_oauth_next"

# GitHub's own OAuth flow already gates against credential brute-forcing
# (there's no password here to guess), but neither /auth/login nor
# /auth/callback had any rate limiting at all - the latter makes two real
# GitHub API calls per hit (_exchange_code_and_fetch_user). Shared budget
# across both endpoints, not a separate one each, since they're the same
# logical flow and a per-endpoint split would just double an attacker's
# effective allowance.
AUTH_RATE_LIMIT = 20
AUTH_RATE_LIMIT_WINDOW_SECONDS = 300

auth_router = APIRouter()
logger = logging.getLogger(__name__)


def _enforce_auth_rate_limit(request: Request) -> None:
    from redis import Redis

    settings = get_settings()
    client_ip = client_ip_from_forwarded_for(
        request.headers.get("x-forwarded-for"),
        request.client.host if request.client else "",
    )
    try:
        rate_limited = is_rate_limited(
            Redis.from_url(settings.redis_url),
            f"ratelimit:auth:{client_ip}",
            AUTH_RATE_LIMIT,
            AUTH_RATE_LIMIT_WINDOW_SECONDS,
        )
    except Exception as exc:  # noqa: BLE001
        # A Redis outage should degrade this endpoint's abuse protection,
        # not take down sign-in itself - fail open, same as the public
        # health rate limit and the Paddle IP allowlist do.
        logger.warning("auth rate limit check failed (%s); allowing request", exc)
        rate_limited = False

    if rate_limited:
        raise HTTPException(
            status_code=429,
            detail="too many requests",
            headers={"Retry-After": str(AUTH_RATE_LIMIT_WINDOW_SECONDS)},
        )


def _derive_key(session_secret: str, purpose: bytes, length: int = 32) -> bytes:
    """One root SESSION_SECRET, cryptographically independent subkeys per
    purpose - encrypting stored GitHub tokens and signing session/oauth
    cookies used to share raw key material, so anything that weakened one
    (a side channel, a future flaw in either primitive) could compromise
    the other for no reason beyond convenience.
    """
    return HKDF(algorithm=hashes.SHA256(), length=length, salt=None, info=purpose).derive(
        session_secret.encode()
    )


def _fernet_key(session_secret: str) -> bytes:
    return base64.urlsafe_b64encode(_derive_key(session_secret, b"aletheore-token-encryption"))


def _signing_secret(session_secret: str) -> str:
    return _derive_key(session_secret, b"aletheore-cookie-signing").hex()


def encrypt_access_token(access_token: str, session_secret: str) -> str:
    return Fernet(_fernet_key(session_secret)).encrypt(access_token.encode()).decode()


def decrypt_access_token(encrypted: str, session_secret: str) -> str:
    return Fernet(_fernet_key(session_secret)).decrypt(encrypted.encode()).decode()


def _github_oauth_http_client() -> httpx.Client:
    return httpx.Client(base_url="https://github.com")


def _github_http_client() -> httpx.Client:
    return httpx.Client(base_url="https://api.github.com")


def _fetch_primary_verified_email(access_token: str) -> str | None:
    # /user's own "email" field only reflects a user's public-profile
    # email setting (often unset) regardless of scope - the verified
    # primary address needs user:email scope plus this separate call.
    response = _github_http_client().get(
        "/user/emails",
        headers={
            "Authorization": f"Bearer {access_token}",
            "Accept": "application/vnd.github+json",
        },
    )
    response.raise_for_status()
    emails = response.json()
    primary = next((e for e in emails if e.get("primary") and e.get("verified")), None)
    if primary:
        return primary["email"]
    verified = next((e for e in emails if e.get("verified")), None)
    return verified["email"] if verified else None


def _exchange_code_and_fetch_user(
    code: str, client_id: str, client_secret: str
) -> tuple[str, str | None, dict, str | None]:
    # Synchronous httpx.Client calls, run off the event loop via
    # asyncio.to_thread by the caller - this whole function otherwise blocks
    # every other request the server is handling for its duration.
    token_response = _github_oauth_http_client().post(
        "/login/oauth/access_token",
        headers={"Accept": "application/json"},
        data={"client_id": client_id, "client_secret": client_secret, "code": code},
    )
    token_response.raise_for_status()
    token_data = token_response.json()
    access_token = token_data["access_token"]
    # Only present when the GitHub App has "Expire user authorization
    # tokens" turned on - absent (None) otherwise, and stored as such.
    refresh_token = token_data.get("refresh_token")

    user_response = _github_http_client().get(
        "/user",
        headers={
            "Authorization": f"Bearer {access_token}",
            "Accept": "application/vnd.github+json",
        },
    )
    user_response.raise_for_status()
    user = user_response.json()

    email = None
    try:
        email = _fetch_primary_verified_email(access_token)
    except httpx.HTTPStatusError:
        # A pre-existing session predating the user:email scope (until the
        # user next consents to it) or a transient GitHub API failure -
        # sign-in must not break because email capture did. Retried
        # automatically on the user's next login.
        pass

    return access_token, refresh_token, user, email


def refresh_github_access_token(
    refresh_token: str, client_id: str, client_secret: str
) -> tuple[str, str | None]:
    """Exchanges a still-valid refresh_token for a new access_token (and
    usually a new refresh_token - GitHub rotates it on each use). Raises
    RuntimeError on failure: GitHub's refresh endpoint returns 200 with an
    `error` field in the body rather than a 4xx status, so a missing
    access_token in an otherwise-successful response is the failure signal.
    """
    response = _github_oauth_http_client().post(
        "/login/oauth/access_token",
        headers={"Accept": "application/json"},
        data={
            "client_id": client_id,
            "client_secret": client_secret,
            "grant_type": "refresh_token",
            "refresh_token": refresh_token,
        },
    )
    response.raise_for_status()
    data = response.json()
    if "access_token" not in data:
        raise RuntimeError(data.get("error_description") or data.get("error") or "refresh failed")
    return data["access_token"], data.get("refresh_token")


def _fetch_installation_details(installation_id: int, app_jwt: str) -> dict:
    return get_installation_details(installation_id, app_jwt, _github_http_client())


def sign_session_id(session_id: str, secret: str) -> str:
    return URLSafeTimedSerializer(_signing_secret(secret)).dumps(session_id)


def unsign_session_id(signed: str, secret: str) -> str | None:
    try:
        return URLSafeTimedSerializer(_signing_secret(secret)).loads(
            signed,
            max_age=int(SESSION_TTL.total_seconds()),
        )
    except BadSignature:
        return None


def sign_oauth_state(state: str, secret: str) -> str:
    return URLSafeTimedSerializer(_signing_secret(secret), salt="oauth-state").dumps(state)


def unsign_oauth_state(signed: str, secret: str) -> str | None:
    try:
        return URLSafeTimedSerializer(_signing_secret(secret), salt="oauth-state").loads(
            signed,
            max_age=int(OAUTH_STATE_TTL.total_seconds()),
        )
    except BadSignature:
        return None


async def get_current_session(request: Request) -> dict | None:
    signed = request.cookies.get(SESSION_COOKIE_NAME)
    if not signed:
        return None
    settings = get_settings()
    session_id = unsign_session_id(signed, settings.session_secret)
    if session_id is None:
        return None
    row = await get_session(request.app.state.db_pool, session_id)
    if row is None:
        return None
    row["github_access_token"] = decrypt_access_token(
        row["github_access_token"], settings.session_secret
    )
    if row["github_refresh_token"] is not None:
        row["github_refresh_token"] = decrypt_access_token(
            row["github_refresh_token"], settings.session_secret
        )
    return row


def _is_safe_next_path(next_path: str | None) -> str:
    if not next_path or not next_path.startswith("/") or next_path.startswith("//"):
        return "/dashboard"
    return next_path


@auth_router.get("/auth/login")
async def login(request: Request, next: str | None = None):
    _enforce_auth_rate_limit(request)
    settings = get_settings()
    state = secrets.token_urlsafe(32)
    safe_next = _is_safe_next_path(next)
    url = (
        "https://github.com/login/oauth/authorize"
        f"?client_id={settings.github_client_id}"
        f"&redirect_uri={settings.public_base_url}/auth/callback"
        f"&state={state}"
    )
    response = RedirectResponse(url=url, status_code=307)
    response.set_cookie(
        OAUTH_STATE_COOKIE_NAME,
        sign_oauth_state(state, settings.session_secret),
        httponly=True,
        secure=True,
        samesite="lax",
        max_age=int(OAUTH_STATE_TTL.total_seconds()),
    )
    response.set_cookie(
        NEXT_COOKIE_NAME,
        safe_next,
        httponly=True,
        secure=True,
        samesite="lax",
        max_age=int(OAUTH_STATE_TTL.total_seconds()),
    )
    return response


@auth_router.get("/auth/callback")
async def callback(code: str, request: Request, state: str | None = None, installation_id: int | None = None):
    _enforce_auth_rate_limit(request)
    settings = get_settings()

    # GitHub redirects here from two different entry points: our own
    # /auth/login (which always sets this cookie and a matching state) and a
    # direct "Install" click on GitHub's own App page, which never goes
    # through /auth/login and so has no state to echo back at all. Only
    # enforce state when we actually set the cookie ourselves.
    signed_state = request.cookies.get(OAUTH_STATE_COOKIE_NAME)
    if signed_state is not None:
        expected_state = unsign_oauth_state(signed_state, settings.session_secret)
        if not expected_state or not state or not hmac.compare_digest(expected_state, state):
            raise HTTPException(status_code=400, detail="invalid oauth state")

    access_token, refresh_token, user, email = await asyncio.to_thread(
        _exchange_code_and_fetch_user, code, settings.github_client_id, settings.github_client_secret
    )

    session_id = secrets.token_urlsafe(32)
    await create_session(
        request.app.state.db_pool,
        session_id,
        user["id"],
        user["login"],
        encrypt_access_token(access_token, settings.session_secret),
        datetime.now(timezone.utc) + SESSION_TTL,
        refresh_token=encrypt_access_token(refresh_token, settings.session_secret)
        if refresh_token
        else None,
    )

    if email:
        # Best-effort, same discipline as the installation upsert below:
        # a captured email (or the welcome email itself) failing to save
        # must never break sign-in, the critical path here.
        try:
            is_new_email = await upsert_github_user_email(request.app.state.db_pool, user["login"], email)
            if is_new_email:
                enqueue_transactional_email(
                    settings.redis_url,
                    dedupe_key=f"welcome:{user['login']}",
                    template_name="welcome",
                    template_arg=user["login"],
                    to_email=email,
                )
        except Exception:
            logger.warning("failed to capture email / enqueue welcome email for %s", user["login"], exc_info=True)

    if installation_id is not None:
        # A direct "Install" click lands here with installation_id already in
        # hand, but the `installations` row it powers /subscribe's checkout
        # page from is normally only written by the async `installation`
        # webhook - which can arrive after this redirect. Upsert it here too
        # so /subscribe never races that webhook. Best-effort: the webhook
        # remains the source of truth, so a failure here just falls back to
        # the normal (slightly delayed) path instead of breaking sign-in.
        try:
            app_jwt = generate_app_jwt(settings.github_app_id, settings.github_app_private_key)
            installation = await asyncio.to_thread(_fetch_installation_details, installation_id, app_jwt)
            await upsert_installation(
                request.app.state.db_pool, installation_id, installation["account"]["login"]
            )
        except Exception:
            logger.warning("failed to synchronously upsert installation %s", installation_id, exc_info=True)

    if signed_state is None and state:
        # A direct "Install" click on GitHub's own App page never goes through
        # /auth/login, so there's no next-cookie - but github_app_install_url()
        # puts our own next_path in `state` for exactly this entry point (it's
        # not a CSRF nonce here, since signed_state is absent and nothing was
        # verified above).
        next_path = _is_safe_next_path(state)
    else:
        next_path = _is_safe_next_path(request.cookies.get(NEXT_COOKIE_NAME))
    response = RedirectResponse(url=next_path, status_code=307)
    response.set_cookie(
        SESSION_COOKIE_NAME,
        sign_session_id(session_id, settings.session_secret),
        httponly=True,
        secure=True,
        samesite="lax",
        max_age=int(SESSION_TTL.total_seconds()),
    )
    response.delete_cookie(OAUTH_STATE_COOKIE_NAME)
    response.delete_cookie(NEXT_COOKIE_NAME)
    return response


@auth_router.get("/auth/logout")
async def logout(request: Request):
    signed = request.cookies.get(SESSION_COOKIE_NAME)
    if signed:
        settings = get_settings()
        session_id = unsign_session_id(signed, settings.session_secret)
        if session_id:
            await delete_session(request.app.state.db_pool, session_id)
    response = RedirectResponse(url="/", status_code=307)
    response.delete_cookie(SESSION_COOKIE_NAME)
    return response
