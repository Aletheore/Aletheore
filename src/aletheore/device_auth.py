from __future__ import annotations

import re
import subprocess
import time
from collections.abc import Callable
from dataclasses import dataclass

from aletheore._lazy import lazy_module
httpx = lazy_module("httpx")


GITHUB_CLIENT_ID = "Iv23liGMhaWSkY927jgI"


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


def _resolved_api_base_url() -> str:
    # Local import: skill_hub_sync imports modules that import this one.
    from aletheore.skill_hub_sync import _api_base_url

    return _api_base_url()


def fetch_my_installations(
    github_token: str,
    api_base_url: str | None = None,
    http_client: httpx.Client | None = None,
) -> list[dict]:
    client = http_client or httpx.Client(base_url=api_base_url or _resolved_api_base_url())
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
    api_base_url: str | None = None,
    http_client: httpx.Client | None = None,
) -> str:
    client = http_client or httpx.Client(base_url=api_base_url or _resolved_api_base_url())
    response = client.post(
        "/v1/cli-tokens",
        headers={"Authorization": f"Bearer {github_token}"},
        json={"installation_id": installation_id, "label": label},
    )
    response.raise_for_status()
    return response.json()["token"]


def mint_personal_token(
    github_token: str,
    label: str,
    api_base_url: str | None = None,
    http_client: httpx.Client | None = None,
) -> str:
    client = http_client or httpx.Client(base_url=api_base_url or _resolved_api_base_url())
    response = client.post(
        "/v1/personal-tokens",
        headers={"Authorization": f"Bearer {github_token}"},
        json={"label": label},
    )
    response.raise_for_status()
    return response.json()["token"]


def revoke_personal_token(
    personal_token: str,
    all_devices: bool = False,
    api_base_url: str | None = None,
    http_client: httpx.Client | None = None,
) -> None:
    client = http_client or httpx.Client(base_url=api_base_url or _resolved_api_base_url())
    path = "/v1/personal-tokens/all" if all_devices else "/v1/personal-tokens"
    response = client.delete(path, headers={"Authorization": f"Bearer {personal_token}"})
    response.raise_for_status()


def revoke_managed_audit_token(
    api_token: str,
    api_base_url: str | None = None,
    http_client: httpx.Client | None = None,
) -> None:
    """Revokes the CLI's own installation token (DELETE /v1/cli-tokens/self).

    The server revokes only the token that authenticates the call. Raises
    httpx.HTTPStatusError on any non-2xx (401 means it was already revoked,
    which the caller treats as done) and httpx.HTTPError on network failure.
    """
    client = http_client or httpx.Client(
        base_url=api_base_url or _resolved_api_base_url(), timeout=_REVOKE_TIMEOUT_SECONDS
    )
    response = client.delete("/v1/cli-tokens/self", headers={"Authorization": f"Bearer {api_token}"})
    response.raise_for_status()


_REVOKE_TIMEOUT_SECONDS = 5.0

DASHBOARD_URL = "https://aletheore.com"


def server_error_detail(exc: httpx.HTTPStatusError) -> str | None:
    """The server's `detail` string from a JSON error body, or None."""
    try:
        body = exc.response.json()
    except Exception:  # noqa: BLE001 - any non-JSON body just means no detail
        return None
    detail = body.get("detail") if isinstance(body, dict) else None
    return detail if isinstance(detail, str) and detail.strip() else None


def describe_login_http_error(exc: httpx.HTTPStatusError) -> str:
    """Actionable text for a failed login whose server answered with an error status."""
    status = exc.response.status_code
    detail = server_error_detail(exc)
    if status == 409:
        reason = detail or "token limit reached"
        return (
            f"You have reached the limit of active CLI tokens for this installation ({reason}). "
            f"Revoke old ones in the dashboard at {DASHBOARD_URL}, or log in from a machine where you "
            "are already logged in, then run `aletheore login` again."
        )
    if status == 402:
        return f"{detail or 'this feature requires the AIR plan'} (login needs a plan that includes managed audits)."
    if detail:
        return f"login failed (HTTP {status}): {detail}"
    return f"login failed (HTTP {status})"


_GITHUB_REMOTE_PREFIXES = (
    "git@github.com:",
    "ssh://git@github.com/",
    "https://github.com/",
    "http://github.com/",
)


def _github_remote_remainder(url: str) -> str | None:
    """Returns "org/repo[.git]" from a github.com remote URL, tolerating embedded credentials."""
    url = re.sub(r"^(https?://)[^/@]+@", r"\1", url.strip())
    for prefix in _GITHUB_REMOTE_PREFIXES:
        if url.startswith(prefix):
            return url[len(prefix):]
    return None


def infer_org_from_cwd_git_remote(
    run_fn: Callable[..., subprocess.CompletedProcess] = subprocess.run,
    cwd: str | None = None,
) -> str | None:
    kwargs = {"capture_output": True, "text": True, "encoding": "utf-8", "timeout": 5, "check": True}
    if cwd is not None:
        kwargs["cwd"] = cwd
    try:
        result = run_fn(["git", "remote", "get-url", "origin"], **kwargs)
    except (subprocess.CalledProcessError, FileNotFoundError, subprocess.TimeoutExpired):
        return None

    remainder = _github_remote_remainder(result.stdout)
    if remainder is None:
        return None
    return remainder.split("/", 1)[0] or None


def infer_repo_full_name_from_cwd_git_remote(
    run_fn: Callable[..., subprocess.CompletedProcess] = subprocess.run,
    cwd: str | None = None,
) -> str | None:
    kwargs = {"capture_output": True, "text": True, "encoding": "utf-8", "timeout": 5, "check": True}
    if cwd is not None:
        kwargs["cwd"] = cwd
    try:
        result = run_fn(["git", "remote", "get-url", "origin"], **kwargs)
    except (subprocess.CalledProcessError, FileNotFoundError, subprocess.TimeoutExpired):
        return None

    remainder = _github_remote_remainder(result.stdout)
    if remainder is None:
        return None
    org, _, repo = remainder.removesuffix("/").removesuffix(".git").partition("/")
    if org and repo and "/" not in repo:
        return f"{org}/{repo}"
    return None


def resolve_installation(
    github_token: str,
    api_base_url: str | None = None,
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
