import time

import httpx
import jwt

from app_server.http_client import get_github_api_client

# Every installation-token mint (every queued job, every webhook handler,
# the dashboard, admin) goes through this one function - it's the single
# chokepoint, so hardening it here covers all of them at once. Bounded to
# one retry, not exponential backoff: a real production failure (2026-09-07,
# run_push_scan_job, job_id 5931fc3d) hit exactly this call with
# RemoteProtocolError ("server disconnected without sending a response"),
# and the identical call from a different job succeeded under 2.5 minutes
# later with no special handling - this was a one-off transient blip on
# GitHub's side, not a persistent outage a longer backoff would be needed
# for. A queued RQ job has nothing else to retry it (unlike a webhook
# delivery, which GitHub itself redelivers on a non-2xx response) - without
# this, a blip here permanently drops that job's scan/review instead of
# recovering on its own.
INSTALLATION_TOKEN_RETRY_DELAY_SECONDS = 1.0


def generate_app_jwt(app_id: str, private_key_pem: str) -> str:
    now = int(time.time())
    payload = {
        "iat": now - 60,
        "exp": now + 540,
        "iss": app_id,
    }
    return jwt.encode(payload, private_key_pem, algorithm="RS256")


def get_installation_token(
    installation_id: int,
    app_jwt: str,
    http_client: httpx.Client | None = None,
) -> str:
    client = http_client or get_github_api_client()
    headers = {
        "Authorization": f"Bearer {app_jwt}",
        "Accept": "application/vnd.github+json",
    }
    path = f"/app/installations/{installation_id}/access_tokens"
    try:
        response = client.post(path, headers=headers)
    except httpx.TransportError:
        # Network-layer failure (connection reset, no response at all) -
        # the one class of error a same-request retry can actually fix.
        # An HTTPStatusError from raise_for_status() below (a real 401/404
        # from GitHub) is deliberately NOT caught here - retrying a bad JWT
        # or a revoked installation wastes the retry on an error that will
        # never resolve itself.
        time.sleep(INSTALLATION_TOKEN_RETRY_DELAY_SECONDS)
        response = client.post(path, headers=headers)
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
