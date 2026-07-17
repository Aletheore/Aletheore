import subprocess
import time
from collections.abc import Callable
from dataclasses import dataclass

import httpx

GITHUB_CLIENT_ID = "Iv23liGMhaWSkY927jgl"


class DeviceFlowError(Exception):
    pass


@dataclass
class DeviceCode:
    device_code: str
    user_code: str
    verification_uri: str
    interval: int
    expires_in: int


def request_device_code(http_client: httpx.Client | None = None) -> DeviceCode:
    client = http_client or httpx.Client(base_url="https://github.com")
    response = client.post(
        "/login/device/code",
        headers={"Accept": "application/json"},
        data={"client_id": GITHUB_CLIENT_ID},
    )
    response.raise_for_status()
    data = response.json()
    return DeviceCode(
        device_code=data["device_code"],
        user_code=data["user_code"],
        verification_uri=data["verification_uri"],
        interval=data["interval"],
        expires_in=data["expires_in"],
    )


def poll_for_access_token(
    code: DeviceCode,
    http_client: httpx.Client | None = None,
    sleep_fn: Callable[[float], None] = time.sleep,
    clock: Callable[[], float] = time.monotonic,
) -> str:
    client = http_client or httpx.Client(base_url="https://github.com")
    deadline = clock() + code.expires_in
    interval = code.interval

    while clock() < deadline:
        sleep_fn(interval)
        response = client.post(
            "/login/oauth/access_token",
            headers={"Accept": "application/json"},
            data={
                "client_id": GITHUB_CLIENT_ID,
                "device_code": code.device_code,
                "grant_type": "urn:ietf:params:oauth:grant-type:device_code",
            },
        )
        response.raise_for_status()
        data = response.json()
        if "access_token" in data:
            return data["access_token"]

        error = data.get("error")
        if error == "authorization_pending":
            continue
        if error == "slow_down":
            interval = data.get("interval", interval + 5)
            continue
        if error == "expired_token":
            raise DeviceFlowError(
                "the code expired before you authorized it - run `aletheore login` again"
            )
        if error == "access_denied":
            raise DeviceFlowError("authorization was denied")
        raise DeviceFlowError(f"unexpected device flow error: {error}")

    raise DeviceFlowError("timed out waiting for authorization")


def fetch_my_installations(
    github_token: str,
    api_base_url: str = "https://app.aletheore.com",
    http_client: httpx.Client | None = None,
) -> list[dict]:
    client = http_client or httpx.Client(base_url=api_base_url)
    response = client.get(
        "/v1/my-installations",
        headers={"Authorization": f"Bearer {github_token}"},
    )
    response.raise_for_status()
    return response.json()["installations"]


def mint_cli_token(
    github_token: str,
    installation_id: int,
    label: str,
    api_base_url: str = "https://app.aletheore.com",
    http_client: httpx.Client | None = None,
) -> str:
    client = http_client or httpx.Client(base_url=api_base_url)
    response = client.post(
        "/v1/cli-tokens",
        headers={"Authorization": f"Bearer {github_token}"},
        json={"installation_id": installation_id, "label": label},
    )
    response.raise_for_status()
    return response.json()["token"]


def infer_org_from_cwd_git_remote(
    run_fn: Callable[..., subprocess.CompletedProcess] = subprocess.run,
) -> str | None:
    try:
        result = run_fn(
            ["git", "remote", "get-url", "origin"],
            capture_output=True,
            text=True,
            timeout=5,
            check=True,
        )
    except (subprocess.CalledProcessError, FileNotFoundError, subprocess.TimeoutExpired):
        return None

    url = result.stdout.strip()
    for prefix in ("git@github.com:", "https://github.com/", "http://github.com/"):
        if url.startswith(prefix):
            remainder = url[len(prefix):]
            org = remainder.split("/", 1)[0]
            return org or None
    return None


def resolve_installation(
    github_token: str,
    api_base_url: str = "https://app.aletheore.com",
    http_client: httpx.Client | None = None,
    run_fn: Callable[..., subprocess.CompletedProcess] = subprocess.run,
) -> dict | list[dict]:
    installations = fetch_my_installations(github_token, api_base_url, http_client)
    if not installations:
        raise DeviceFlowError(
            "no paid Aletheore installations found for your GitHub account - "
            "install or upgrade the app first at https://github.com/apps/aletheore"
        )

    inferred_org = infer_org_from_cwd_git_remote(run_fn)
    if inferred_org:
        matches = [
            installation
            for installation in installations
            if installation["account_login"] == inferred_org
        ]
        if len(matches) == 1:
            return matches[0]

    return installations
