# Aletheore CLI GitHub Device Flow (`aletheore login`)

**Status:** Draft, pending review
**Date:** 2026-07-18

## Problem

The only way to get a personal API token for managed audits today is: log into the hosted
dashboard in a browser (`app.aletheore.com`), open the admin page for a specific org/repo, click
"generate token," and copy-paste the raw value into a terminal (`--token` flag, `ALETHEORE_API_TOKEN`
env var, or the CLI's save-locally prompt). That's real friction for a CLI-first tool.

GitHub OAuth's **device flow** solves this the way `gh auth login` does: the CLI shows a short
code, the user enters it at `github.com/login/device` in whatever browser they already have open,
and the CLI picks up a real GitHub token with no local callback server and no copy-pasting. This
is a separate mechanism from the OAuth **web** flow already built for the hosted dashboard
(`app_server/auth.py`'s `/auth/login` → `/auth/callback`) - that flow authenticates a browser
session; this one authenticates a terminal. Both end up producing an Aletheore personal API token
through the same underlying `api_tokens` table and seat-cap logic - device flow is just a second,
CLI-native way to reach it.

## Goals

- New `aletheore login` CLI command: GitHub device-flow auth, then mints and saves a personal API
  token for the resolved org/repo's paid installation, using the exact same storage/consumption
  path the `--token`/`ALETHEORE_API_TOKEN` flow already reads (`credentials.py`,
  provider name `aletheore-managed-audit`) - zero changes needed in `_managed_audit` itself.
- Org/repo resolution: infer from `git remote get-url origin` in the current directory first; if
  that doesn't resolve to exactly one administered paid installation, fetch the user's administered
  paid installations and prompt them to pick from a numbered list.
- Two new backend endpoints, both authenticated via `Authorization: Bearer <github_token>` (no
  session cookie - CLI-only):
  - `GET /v1/my-installations` - paid installations the token holder administers.
  - `POST /v1/cli-tokens` - mint a token for a chosen `installation_id`.
- Reuse the existing `create_api_token`/`get_max_tokens`/`count_active_tokens` seat-cap logic and
  the existing admin-check logic (`/user/installations` cross-reference) exactly as already
  implemented in `admin.py` - refactored into shared helpers, not duplicated.

## Non-Goals

- **No lazy auto-trigger.** `_managed_audit` (the existing `--token`/`ALETHEORE_API_TOKEN`
  consumer) is not changed to auto-invoke device flow when no token is found. `aletheore login` is
  an explicit, separate command, matching `gh auth login`'s own UX exactly.
- **No new revoke UI.** A token minted via device flow shows up in the existing dashboard token
  list (`GET /admin/{org}/{repo}`) like any other, and can already be revoked there
  (`DELETE /admin/{org}/{repo}/tokens/{token_id}`). Nothing new needed.
- **No persistence of the raw GitHub access token.** It's used once, in memory, to call the two
  new endpoints, then discarded - only the resulting Aletheore token is saved to disk. Least
  retention, consistent with the project's existing privacy posture (e.g. the GitHub App backend
  already only transiently uses cloned source, never storing it).
- **No reuse of `/admin/{org}/{repo}/tokens` for the CLI.** That endpoint is path-keyed by
  org/repo but a token is actually scoped to an `installation_id` (which can cover multiple
  repos) - forcing the CLI to guess an arbitrary repo name just to hit that URL would be a real
  correctness smell, not a shortcut. The new `installation_id`-keyed endpoint is the correct fix,
  not scope creep.
- **Confirmation prompt on re-login.** Running `aletheore login` again just overwrites the saved
  token with a one-line note, no `[y/N]` gate - old tokens are revocable from the dashboard if
  that ever matters.

## Architecture

### Backend: `github-app/app_server/admin.py`

Refactor the inline logic inside `_require_admin_installation` into two reusable pieces:

```python
def _github_http_client() -> httpx.Client:      # already exists, unchanged
    return httpx.Client(base_url="https://api.github.com")

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
```

`_require_admin_installation` (existing, session-cookie-based) keeps working exactly as today -
it just calls `_administered_installation_ids` internally instead of inlining the same three
lines. No behavior change for the existing browser-authenticated routes.

Two new routes, same file:

```python
@admin_router.get("/v1/my-installations")
async def my_installations(request: Request):
    github_token = _bearer_github_token(request)
    administered_ids = await _administered_installation_ids(github_token)
    pool = request.app.state.db_pool
    rows = await pool.fetch(
        """
        SELECT installation_id, account_login
        FROM installations
        WHERE installation_id = ANY($1::bigint[]) AND plan != 'free'
        """,
        list(administered_ids),
    )
    return {"installations": [dict(r) for r in rows]}


@admin_router.post("/v1/cli-tokens")
async def create_cli_token(request: Request):
    github_token = _bearer_github_token(request)
    body = await request.json()
    installation_id = body["installation_id"]
    label = body["label"]

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
    await create_api_token(pool, installation_id, token_hash, label, installation["account_login"])
    token_id = (await list_api_tokens(pool, installation_id))[0]["id"]
    return {"token": raw_token, "id": token_id, "label": label}
```

`installations.account_login` is passed as the token's "created by" field (mirroring the existing
column's use for the session-based path, which stores `session["github_login"]` - here there's no
session, so the installation's own account login is the closest equivalent, since the device-flow
GitHub identity isn't persisted anywhere to reference later).

### Prerequisite: GitHub App setting

Device flow must be toggled on for the App itself (`github.com/settings/apps/aletheore` -> "Device
Flow" checkbox under general settings). This is a one-time manual step, same category as the
webhook-URL/callback-URL changes already made by hand earlier - not something automatable from
this repo.

### CLI: `prototype/aletheore/credentials.py`

Add one public wrapper around the existing private save function - no other changes:

```python
def save_api_token(provider_name: str, token: str, credentials_path: Path = DEFAULT_CREDENTIALS_PATH) -> None:
    _save_key(provider_name, token, credentials_path)
```

### CLI: `prototype/aletheore/device_auth.py` (new file)

Owns the device-flow HTTP mechanics and org/repo resolution, kept separate from `cli.py` so the
`login` command in `cli.py` stays a thin orchestration wrapper (consistent with how `cli.py`
already delegates to `evidence.py`, `history.py`, etc. rather than inlining logic):

```python
import subprocess
import time
from dataclasses import dataclass

import httpx

GITHUB_CLIENT_ID = "Iv23liGMhaWSkY927jgI"  # public, not a secret - same App as the web flow
DEVICE_BASE_URL = "https://github.com"
BACKEND_BASE_URL = "https://app.aletheore.com"


class DeviceFlowError(Exception):
    pass


@dataclass
class DeviceCode:
    device_code: str
    user_code: str
    verification_uri: str
    interval: int
    expires_in: int


def request_device_code() -> DeviceCode:
    response = httpx.post(
        f"{DEVICE_BASE_URL}/login/device/code",
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


def poll_for_access_token(code: DeviceCode) -> str:
    deadline = time.monotonic() + code.expires_in
    interval = code.interval
    while time.monotonic() < deadline:
        time.sleep(interval)
        response = httpx.post(
            f"{DEVICE_BASE_URL}/login/oauth/access_token",
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
            raise DeviceFlowError("the code expired before you authorized it - run `aletheore login` again")
        if error == "access_denied":
            raise DeviceFlowError("authorization was denied")
        raise DeviceFlowError(f"unexpected device flow error: {error}")
    raise DeviceFlowError("timed out waiting for authorization")


def fetch_my_installations(github_token: str) -> list[dict]:
    response = httpx.get(
        f"{BACKEND_BASE_URL}/v1/my-installations",
        headers={"Authorization": f"Bearer {github_token}"},
    )
    response.raise_for_status()
    return response.json()["installations"]


def mint_cli_token(github_token: str, installation_id: int, label: str) -> str:
    response = httpx.post(
        f"{BACKEND_BASE_URL}/v1/cli-tokens",
        headers={"Authorization": f"Bearer {github_token}"},
        json={"installation_id": installation_id, "label": label},
    )
    response.raise_for_status()
    return response.json()["token"]


def infer_org_from_cwd_git_remote() -> str | None:
    try:
        result = subprocess.run(
            ["git", "remote", "get-url", "origin"],
            capture_output=True, text=True, timeout=5, check=True,
        )
    except (subprocess.CalledProcessError, FileNotFoundError, subprocess.TimeoutExpired):
        return None
    url = result.stdout.strip()
    # matches git@github.com:org/repo.git and https://github.com/org/repo(.git)
    for prefix in ("git@github.com:", "https://github.com/", "http://github.com/"):
        if url.startswith(prefix):
            remainder = url[len(prefix):]
            org = remainder.split("/", 1)[0]
            return org or None
    return None


def resolve_installation(github_token: str) -> dict:
    installations = fetch_my_installations(github_token)
    if not installations:
        raise DeviceFlowError(
            "no paid Aletheore installations found for your GitHub account - "
            "install or upgrade the app first at https://github.com/apps/aletheore"
        )

    inferred_org = infer_org_from_cwd_git_remote()
    if inferred_org:
        matches = [i for i in installations if i["account_login"] == inferred_org]
        if len(matches) == 1:
            return matches[0]

    return installations  # caller prompts when this isn't a single dict
```

`resolve_installation` returning either a single dict (auto-resolved) or the full list (needs a
prompt) is an intentional union return, matched with an `isinstance(result, dict)` check at the
call site in `cli.py` - keeping the prompting/`rich`-formatted UI in `cli.py` where the rest of the
CLI's interactive UI already lives, while `device_auth.py` stays UI-free and independently
testable.

### CLI: `prototype/aletheore/cli.py`

New command, following the existing `@app.command(help=...)` pattern used by every other
subcommand in this file:

```python
@app.command(help="authenticate with GitHub via device flow and save a personal API token")
def login():
    from aletheore.device_auth import (
        DeviceFlowError, mint_cli_token, poll_for_access_token,
        request_device_code, resolve_installation,
    )
    import socket

    try:
        code = request_device_code()
        console.print(f"First, authenticate with GitHub:")
        console.print(f"  1. Go to: [bold]{code.verification_uri}[/bold]")
        console.print(f"  2. Enter code: [bold cyan]{code.user_code}[/bold cyan]")
        console.print("Waiting for authorization...")
        github_token = poll_for_access_token(code)

        result = resolve_installation(github_token)
        if isinstance(result, dict):
            installation = result
        else:
            console.print("Multiple paid installations found - pick one:")
            for i, inst in enumerate(result, start=1):
                console.print(f"  {i}. {inst['account_login']}")
            choice = int(input("Enter a number: "))
            installation = result[choice - 1]

        label = f"{socket.gethostname()} (device flow)"
        token = mint_cli_token(github_token, installation["installation_id"], label)
        save_api_token("aletheore-managed-audit", token)
        console.print(
            f"[bold green]Logged in.[/bold green] Token saved for "
            f"[bold]{installation['account_login']}[/bold]. "
            f"This replaces any previously saved token."
        )
    except DeviceFlowError as e:
        console.print(f"[bold red]error:[/bold red] {e}")
        raise typer.Exit(code=1)
```

## Testing

- `_administered_installation_ids` and the two new routes: real-Postgres tests in
  `tests/test_admin.py` following this file's existing style - seed an `installations` row with
  `plan='pro'`, mock the `/user/installations` GitHub call, assert `/v1/my-installations` returns
  it and `/v1/cli-tokens` mints a real row via `create_api_token` respecting the seat cap.
- A free-plan installation is correctly excluded from `/v1/my-installations` and rejected (402)
  by `/v1/cli-tokens`.
- An installation the token holder doesn't administer is rejected (403) by both new routes.
- Seat-cap enforcement on `/v1/cli-tokens` mirrors the existing test for the session-based route
  (409 once `max_tokens` active tokens already exist).
- `infer_org_from_cwd_git_remote`: unit tests covering `git@github.com:org/repo.git`,
  `https://github.com/org/repo`, `https://github.com/org/repo.git`, a non-GitHub remote (returns
  `None`), and no git repo present at all (returns `None`, not an exception).
- `poll_for_access_token`: unit tests mocking the GitHub token endpoint for each of
  `authorization_pending` (keeps polling), `slow_down` (adjusts interval), `expired_token` and
  `access_denied` (both raise `DeviceFlowError` with the right message), and a real success case.
- `resolve_installation`: unit tests for the auto-resolved-single-match case, the
  needs-a-prompt case (ambiguous or no match), and the empty-installations `DeviceFlowError` case.

## Success Criteria

1. A real GitHub account administering a paid Aletheore installation can run `aletheore login`
   inside that repo's local clone, complete the device-flow prompt at `github.com/login/device`,
   and end up with a working token - verified by then running an existing managed-audit command
   with no `--token`/`ALETHEORE_API_TOKEN` set and seeing it succeed using the saved token.
2. The same account, run from outside any git repo (or a repo whose remote doesn't match an
   administered installation), gets prompted with a numbered list and picks correctly.
3. A free-plan-only GitHub account gets a clear, correct error pointing at installing/upgrading -
   not a stack trace or a confusing 500.
