import time

import httpx
import jwt

from app_server.http_client import get_github_api_client


def generate_app_jwt(app_id: str, private_key_pem: str) -> str:
    now = int(time.time())
    payload = {
        "iat": now - 60,
        "exp": now + 600,
        "iss": app_id,
    }
    return jwt.encode(payload, private_key_pem, algorithm="RS256")


def get_installation_token(
    installation_id: int,
    app_jwt: str,
    http_client: httpx.Client | None = None,
) -> str:
    client = http_client or get_github_api_client()
    response = client.post(
        f"/app/installations/{installation_id}/access_tokens",
        headers={
            "Authorization": f"Bearer {app_jwt}",
            "Accept": "application/vnd.github+json",
        },
    )
    response.raise_for_status()
    return response.json()["token"]


def get_installation_details(
    installation_id: int,
    app_jwt: str,
    http_client: httpx.Client | None = None,
) -> dict:
    client = http_client or get_github_api_client()
    response = client.get(
        f"/app/installations/{installation_id}",
        headers={
            "Authorization": f"Bearer {app_jwt}",
            "Accept": "application/vnd.github+json",
        },
    )
    response.raise_for_status()
    return response.json()


def get_repo_permission_for_user(
    repo_full_name: str,
    username: str,
    installation_token: str,
    http_client: httpx.Client | None = None,
) -> str:
    """The caller's permission level on repo_full_name - "admin", "write",
    "read", or "none". Gates any webhook-triggered action that should only
    be available to someone who could already push to the repo: an issue
    comment fires for anyone who can comment (on a public repo, anyone
    with a GitHub account), which is a much wider set than anyone who
    should be able to spend an installation's paid LLM budget or occupy
    its managed-audit cooldown slot.
    """
    client = http_client or get_github_api_client()
    response = client.get(
        f"/repos/{repo_full_name}/collaborators/{username}/permission",
        headers={
            "Authorization": f"token {installation_token}",
            "Accept": "application/vnd.github+json",
        },
    )
    response.raise_for_status()
    return response.json()["permission"]
