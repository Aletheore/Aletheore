import base64
import hashlib
import secrets
from datetime import datetime, timedelta, timezone

import httpx
from cryptography.fernet import Fernet
from fastapi import APIRouter, Request
from fastapi.responses import RedirectResponse
from itsdangerous import BadSignature, URLSafeTimedSerializer

from app_server.config import get_settings
from app_server.db import create_session, delete_session, get_session

SESSION_COOKIE_NAME = "session"
SESSION_TTL = timedelta(days=30)

auth_router = APIRouter()


def _fernet_key(session_secret: str) -> bytes:
    return base64.urlsafe_b64encode(hashlib.sha256(session_secret.encode()).digest())


def encrypt_access_token(access_token: str, session_secret: str) -> str:
    return Fernet(_fernet_key(session_secret)).encrypt(access_token.encode()).decode()


def decrypt_access_token(encrypted: str, session_secret: str) -> str:
    return Fernet(_fernet_key(session_secret)).decrypt(encrypted.encode()).decode()


def _github_oauth_http_client() -> httpx.Client:
    return httpx.Client(base_url="https://github.com")


def _github_http_client() -> httpx.Client:
    return httpx.Client(base_url="https://api.github.com")


def sign_session_id(session_id: str, secret: str) -> str:
    return URLSafeTimedSerializer(secret).dumps(session_id)


def unsign_session_id(signed: str, secret: str) -> str | None:
    try:
        return URLSafeTimedSerializer(secret).loads(
            signed,
            max_age=int(SESSION_TTL.total_seconds()),
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
    return row


@auth_router.get("/auth/login")
async def login():
    settings = get_settings()
    url = (
        "https://github.com/login/oauth/authorize"
        f"?client_id={settings.github_client_id}"
        f"&redirect_uri={settings.public_base_url}/auth/callback"
    )
    return RedirectResponse(url=url, status_code=307)


@auth_router.get("/auth/callback")
async def callback(code: str, request: Request):
    settings = get_settings()

    token_response = _github_oauth_http_client().post(
        "/login/oauth/access_token",
        headers={"Accept": "application/json"},
        data={
            "client_id": settings.github_client_id,
            "client_secret": settings.github_client_secret,
            "code": code,
        },
    )
    token_response.raise_for_status()
    access_token = token_response.json()["access_token"]

    user_response = _github_http_client().get(
        "/user",
        headers={
            "Authorization": f"Bearer {access_token}",
            "Accept": "application/vnd.github+json",
        },
    )
    user_response.raise_for_status()
    user = user_response.json()

    session_id = secrets.token_urlsafe(32)
    await create_session(
        request.app.state.db_pool,
        session_id,
        user["id"],
        user["login"],
        encrypt_access_token(access_token, settings.session_secret),
        datetime.now(timezone.utc) + SESSION_TTL,
    )

    response = RedirectResponse(url="/dashboard", status_code=307)
    response.set_cookie(
        SESSION_COOKIE_NAME,
        sign_session_id(session_id, settings.session_secret),
        httponly=True,
        secure=True,
        samesite="lax",
        max_age=int(SESSION_TTL.total_seconds()),
    )
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
