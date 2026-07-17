import time

import httpx
import jwt


def generate_app_jwt(app_id: str, private_key_pem: str) -> str:
    now = int(time.time())
    payload = {
        "iat": now - 60,
        "exp": now + 600,
        "iss": app_id,
    }
    return jwt.encode(payload, private_key_pem, algorithm="RS256")


async def get_installation_token(
    installation_id: int,
    app_jwt: str,
    http_client: httpx.Client | None = None,
) -> str:
    client = http_client or httpx.Client(base_url="https://api.github.com")
    response = client.post(
        f"/app/installations/{installation_id}/access_tokens",
        headers={
            "Authorization": f"Bearer {app_jwt}",
            "Accept": "application/vnd.github+json",
        },
    )
    response.raise_for_status()
    return response.json()["token"]
