import logging
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, HTTPException, Request, Response

from aletheore.evidence_resolution import resolve_code_evidence
from app_server.admin import (
    _administered_installation_ids_for_session_or_401,
    _github_http_client,
    _repo_installation_id,
    _require_admin_installation,
    _require_seat_if_paid,
)
from app_server.auth import get_current_session
from app_server.config import get_settings
from app_server.db import (
    MAX_SCANNED_REPOS_PER_MONTH,
    count_monthly_scanned_repos,
    get_docs_build_status,
    get_endpoint_health_history,
    get_endpoint_health_summary_since,
    get_endpoint_uptime_pct_since,
    get_installation,
    get_latest_evidence,
    get_recent_endpoint_health,
    get_recent_history,
    get_wiki_build_status,
    get_wiki_overview,
    get_wiki_subsystem,
    list_docs_symbols,
    list_repos_for_installations,
    list_wiki_subsystems,
)
from app_server.github_auth import generate_app_jwt, get_installation_token

dashboard_router = APIRouter()
MIN_CHECKS_FOR_STALE_CONFIDENCE = 5
STALE_ENDPOINT_WINDOW_DAYS = 30


def find_stale_endpoints(
    endpoints: list[dict], health_summary: dict[tuple[str, str], dict]
) -> list[dict]:
    stale = []
    for endpoint in endpoints:
        key = (endpoint.get("method"), endpoint.get("path"))
        summary = health_summary.get(key)
        if summary is None:
            continue
        if summary["ever_reachable"] or summary["check_count"] < MIN_CHECKS_FOR_STALE_CONFIDENCE:
            continue
        stale.append(
            {
                "method": endpoint.get("method"),
                "path": endpoint.get("path"),
                "file": endpoint.get("file"),
                "line": endpoint.get("line"),
                "check_count": summary["check_count"],
            }
        )
    return stale


async def _uninitialized_repos_for_installation(
    installation_id: int, plan: str, already_known: set[str], scan_limit_reached: bool = False
) -> list[dict]:
    """Repos a GitHub App installation covers that have never completed a
    scan yet. Installing (or paying for) an installation creates no
    per-repo record by itself - webhooks/installation.py's
    handle_installation_event only upserts the installations table, and a
    repo only gets a repo_history row once its first scan actually
    completes. Without this, a freshly installed or freshly upgraded
    installation shows nothing at all in "Your repositories" until that
    first scan finishes (which can take minutes), with no feedback in the
    meantime - confirmed as a real gap dogfooding this against a live
    installation.

    Best-effort: this is a page enrichment, not the primary data - ANY
    failure (a bad/missing app key, a revoked installation, a rate limit, a
    GitHub outage, a network error) returns an empty list rather than
    failing the whole page. A user's already-scanned repos must still load
    even if this best-effort lookup can't run at all.
    """
    try:
        settings = get_settings()
        app_jwt = generate_app_jwt(settings.github_app_id, settings.github_app_private_key)
        token = await get_installation_token(installation_id, app_jwt)
        response = _github_http_client().get(
            "/installation/repositories",
            headers={
                "Authorization": f"Bearer {token}",
                "Accept": "application/vnd.github+json",
            },
        )
        response.raise_for_status()
    except Exception:
        return []

    result = []
    for repo in response.json().get("repositories", []):
        full_name = repo["full_name"]
        if full_name in already_known:
            continue
        org, _, repo_name = full_name.partition("/")
        result.append(
            {
                "org": org,
                "repo": repo_name,
                "repo_full_name": full_name,
                "plan": plan,
                "initialized": False,
                "scan_limit_reached": scan_limit_reached,
            }
        )
    return result


@dashboard_router.get("/app/repos")
async def list_my_repos(request: Request):
    session = await get_current_session(request)
    if session is None:
        raise HTTPException(status_code=401, detail="login required")

    pool = request.app.state.db_pool
    administered_ids = await _administered_installation_ids_for_session_or_401(pool, session)
    repos = await list_repos_for_installations(pool, list(administered_ids))
    result = []
    known_by_installation: dict[int, set[str]] = {}
    for row in repos:
        known_by_installation.setdefault(row["installation_id"], set()).add(row["repo_full_name"])
        # The hosted dashboard is an AIR (paid) feature. Community is free,
        # self-service, and unmanaged by design - the CLI, the free GitHub
        # Action, and free GitHub App usage are all meant to work without
        # ever touching a shared web view. Listing a free installation's
        # repos here would let every GitHub admin on that org click into a
        # full managed dashboard for free - exactly the team-collaboration
        # capability AIR itself sells.
        if row["plan"] == "free":
            continue
        # repo_full_name is the source of truth for the org/repo split used
        # in every /app/{org}/{repo} route - account_login is a display
        # value only and isn't guaranteed to match the org segment exactly.
        org, _, repo = row["repo_full_name"].partition("/")
        result.append(
            {
                "org": org,
                "repo": repo,
                "repo_full_name": row["repo_full_name"],
                "plan": row["plan"],
                "initialized": True,
            }
        )

    for installation_id in administered_ids:
        installation = await get_installation(pool, installation_id)
        if installation is None or installation["plan"] == "free":
            continue
        known = known_by_installation.get(installation_id, set())
        scanned_this_month = await count_monthly_scanned_repos(pool, installation_id)
        scan_limit_reached = scanned_this_month >= MAX_SCANNED_REPOS_PER_MONTH
        result.extend(
            await _uninitialized_repos_for_installation(
                installation_id, installation["plan"], known, scan_limit_reached
            )
        )

    return {"repos": result}


async def _require_dashboard_installation(request: Request, org: str, repo: str) -> int:
    # Session + ownership check first, before any repo lookup - an unauthenticated
    # caller should not learn whether a given org/repo has scan history at all.
    session = await get_current_session(request)
    if session is None:
        raise HTTPException(status_code=401, detail="login required")

    pool = request.app.state.db_pool
    installation_id = await _repo_installation_id(pool, org, repo)

    administered_ids = await _administered_installation_ids_for_session_or_401(pool, session)
    if installation_id not in administered_ids:
        raise HTTPException(status_code=403, detail="you do not administer this installation")

    installation = await get_installation(pool, installation_id)
    if installation is not None:
        if installation["plan"] == "free":
            raise HTTPException(status_code=402, detail="the managed dashboard requires a paid plan")
        await _require_seat_if_paid(pool, installation, session["github_login"])

    return installation_id


@dashboard_router.get("/app/{org}/{repo}")
async def get_dashboard(org: str, repo: str, request: Request):
    installation_id = await _require_dashboard_installation(request, org, repo)
    pool = request.app.state.db_pool
    repo_full_name = f"{org}/{repo}"
    history = await get_recent_history(pool, installation_id, repo_full_name)
    return {"repo_full_name": repo_full_name, "history": history}


@dashboard_router.get("/app/{org}/{repo}/health")
async def get_dashboard_health(org: str, repo: str, request: Request):
    installation_id = await _require_dashboard_installation(request, org, repo)
    pool = request.app.state.db_pool
    repo_full_name = f"{org}/{repo}"

    evidence = await get_latest_evidence(pool, installation_id, repo_full_name)
    rows = await get_recent_endpoint_health(pool, installation_id, repo_full_name)

    endpoints = []
    for row in rows:
        entry = {
            "target_id": row["target_id"],
            "target_label": row["target_label"],
            "method": row["endpoint_method"],
            "path": row["endpoint_path"],
            "reachable": row["reachable"],
            "status_code": row["status_code"],
            "latency_ms": float(row["latency_ms"]) if row["latency_ms"] is not None else None,
            "checked_at": row["checked_at"].isoformat(),
        }
        if evidence is not None:
            entry["evidence_resolution"] = resolve_code_evidence(
                evidence,
                kind="endpoint",
                method=row["endpoint_method"],
                path=row["endpoint_path"],
            )
        endpoints.append(entry)

    since = datetime.now(timezone.utc) - timedelta(days=STALE_ENDPOINT_WINDOW_DAYS)
    health_summary = await get_endpoint_health_summary_since(
        pool,
        installation_id,
        repo_full_name,
        since,
    )
    api_endpoints = (
        (evidence or {})
        .get("repository", {})
        .get("api_endpoints", {})
        .get("endpoints", [])
    )
    stale_endpoints = find_stale_endpoints(api_endpoints, health_summary)

    return {
        "repo_full_name": repo_full_name,
        "endpoints": endpoints,
        "stale_endpoints": stale_endpoints,
    }


@dashboard_router.get("/app/{org}/{repo}/health/history")
async def get_dashboard_health_history(
    org: str,
    repo: str,
    request: Request,
    method: str,
    path: str,
    target_id: int | None = None,
    limit: int = 50,
):
    installation_id = await _require_dashboard_installation(request, org, repo)
    pool = request.app.state.db_pool
    repo_full_name = f"{org}/{repo}"

    rows = await get_endpoint_health_history(
        pool, installation_id, repo_full_name, target_id, method, path, limit
    )
    return {
        "repo_full_name": repo_full_name,
        "method": method,
        "path": path,
        "checks": [
            {
                "reachable": row["reachable"],
                "status_code": row["status_code"],
                "latency_ms": float(row["latency_ms"]) if row["latency_ms"] is not None else None,
                "checked_at": row["checked_at"].isoformat(),
            }
            for row in rows
        ],
    }


@dashboard_router.get("/app/{org}/{repo}/wiki")
async def get_dashboard_wiki(org: str, repo: str, request: Request):
    installation = await _require_admin_installation(request, org, repo)
    pool = request.app.state.db_pool
    installation_id = installation["installation_id"]
    repo_full_name = f"{org}/{repo}"

    overview = await get_wiki_overview(pool, installation_id, repo_full_name)
    if overview is not None:
        overview["updated_at"] = overview["updated_at"].isoformat()

    build_status = await get_wiki_build_status(pool, installation_id, repo_full_name)

    subsystems = await list_wiki_subsystems(pool, installation_id, repo_full_name)
    return {
        "repo_full_name": repo_full_name,
        "overview": overview,
        "build_status": build_status["status"] if build_status is not None else None,
        "build_error": build_status["error_message"] if build_status is not None else None,
        "subsystems": [
            {
                "subsystem_id": s["subsystem_id"],
                "name": s["name"],
                "description": s["description"],
                "diagram_mermaid": s["diagram_mermaid"],
                "updated_at": s["updated_at"].isoformat(),
            }
            for s in subsystems
        ],
    }


@dashboard_router.get("/app/{org}/{repo}/wiki/{subsystem_id}")
async def get_dashboard_wiki_subsystem(org: str, repo: str, subsystem_id: str, request: Request):
    installation = await _require_admin_installation(request, org, repo)
    pool = request.app.state.db_pool
    repo_full_name = f"{org}/{repo}"

    subsystem = await get_wiki_subsystem(pool, installation["installation_id"], repo_full_name, subsystem_id)
    if subsystem is None:
        raise HTTPException(status_code=404, detail="subsystem not found")

    subsystem["updated_at"] = subsystem["updated_at"].isoformat()
    return {"repo_full_name": repo_full_name, "subsystem": subsystem}


@dashboard_router.get("/app/{org}/{repo}/docs")
async def get_dashboard_docs(org: str, repo: str, request: Request):
    """Grounded API reference (docs_reference.py) merged with whatever
    AI-generated/polished descriptions live_docs.py has stored, exactly
    the same way the pure-evidence CLI/query path renders it - this route
    is the only place that gets to use ai_descriptions_by_module, since
    it's the paid-plan-gated one (_require_admin_installation's own
    "plan == free" -> 402 already covers this, same as /wiki).
    """
    from aletheore.docs_reference import build_api_reference

    installation = await _require_admin_installation(request, org, repo)
    pool = request.app.state.db_pool
    installation_id = installation["installation_id"]
    repo_full_name = f"{org}/{repo}"

    evidence = await get_latest_evidence(pool, installation_id, repo_full_name)
    build_status = await get_docs_build_status(pool, installation_id, repo_full_name)
    if evidence is None:
        return {
            "repo_full_name": repo_full_name,
            "modules": {},
            "build_status": build_status["status"] if build_status is not None else None,
            "build_error": build_status["error_message"] if build_status is not None else None,
        }

    symbols = await list_docs_symbols(pool, installation_id, repo_full_name)
    ai_descriptions_by_module: dict[str, dict[str, dict]] = {}
    for row in symbols:
        ai_descriptions_by_module.setdefault(row["module_path"], {})[row["symbol_name"]] = {
            "description": row["description"],
            "mode": row["mode"],
        }

    modules = build_api_reference(evidence, ai_descriptions_by_module)
    return {
        "repo_full_name": repo_full_name,
        "modules": modules,
        "build_status": build_status["status"] if build_status is not None else None,
        "build_error": build_status["error_message"] if build_status is not None else None,
    }


PUBLIC_HEALTH_RATE_LIMIT = 60
PUBLIC_HEALTH_RATE_LIMIT_WINDOW_SECONDS = 60


@dashboard_router.get("/v1/health/{org}/{repo}")
async def get_public_health(org: str, repo: str, request: Request, response: Response):
    response.headers["Access-Control-Allow-Origin"] = "*"

    from redis import Redis

    from app_server.paddle_ip_allowlist import client_ip_from_forwarded_for
    from app_server.rate_limit import is_rate_limited

    client_ip = client_ip_from_forwarded_for(
        request.headers.get("x-forwarded-for"),
        request.client.host if request.client else "",
    )
    try:
        rate_limited = is_rate_limited(
            Redis.from_url(get_settings().redis_url),
            f"ratelimit:public_health:{client_ip}",
            PUBLIC_HEALTH_RATE_LIMIT,
            PUBLIC_HEALTH_RATE_LIMIT_WINDOW_SECONDS,
        )
    except Exception as exc:  # noqa: BLE001
        # A Redis outage should degrade this endpoint's abuse protection,
        # not take down the public status API itself - fail open, same as
        # the Paddle IP allowlist does when it can't reach Paddle's /ips.
        logging.getLogger("app_server.dashboard").warning(
            "public health rate limit check failed (%s); allowing request", exc
        )
        rate_limited = False

    if rate_limited:
        raise HTTPException(
            status_code=429,
            detail="too many requests",
            headers={
                "Access-Control-Allow-Origin": "*",
                "Retry-After": str(PUBLIC_HEALTH_RATE_LIMIT_WINDOW_SECONDS),
            },
        )

    repo_full_name = f"{org}/{repo}"
    rows = await request.app.state.db_pool.fetch(
        """
        SELECT DISTINCT ON (endpoint_method, endpoint_path)
            endpoint_method, endpoint_path, reachable, status_code, latency_ms, checked_at
        FROM endpoint_health
        WHERE repo_full_name = $1
        ORDER BY endpoint_method, endpoint_path, checked_at DESC, id DESC
        """,
        repo_full_name,
    )
    if not rows:
        raise HTTPException(
            status_code=404,
            detail="no health data for this repo",
            headers={"Access-Control-Allow-Origin": "*"},
        )

    # An aggregate 7-day uptime percentage, not raw history - this is a
    # public, unauthenticated, CORS-open endpoint, so it gets a trend
    # signal without handing out granular check-by-check timing data to
    # anyone who asks (the authenticated dashboard endpoint has that).
    since = datetime.now(timezone.utc) - timedelta(days=7)
    uptime_by_endpoint = await get_endpoint_uptime_pct_since(request.app.state.db_pool, repo_full_name, since)

    return {
        "repo_full_name": repo_full_name,
        "endpoints": [
            {
                "method": row["endpoint_method"],
                "path": row["endpoint_path"],
                "reachable": row["reachable"],
                "status_code": row["status_code"],
                "latency_ms": float(row["latency_ms"]) if row["latency_ms"] is not None else None,
                "checked_at": row["checked_at"].isoformat(),
                "uptime_pct_7d": uptime_by_endpoint.get((row["endpoint_method"], row["endpoint_path"])),
            }
            for row in rows
        ],
    }
