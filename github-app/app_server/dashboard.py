import asyncio
import logging
import re
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, HTTPException, Request, Response

from aletheore.evidence_resolution import resolve_code_evidence
from scan_worker.github_api import fetch_file_content
from scan_worker.live_wiki import build_file_fallback_detail
from app_server.admin import (
    _administered_installation_ids_for_session_or_401,
    _github_http_client,
    _repo_installation_id,
    _require_admin_installation,
    _require_seat_if_paid,
)
from app_server.auth import get_current_session
from app_server.config import get_settings
from app_server.dismissed_findings import dismiss_finding, get_dismissed_identity_keys, undismiss_finding
from app_server.db import (
    MAX_SCANNED_REPOS_PER_MONTH,
    count_monthly_scanned_repos,
    get_docs_build_status,
    get_endpoint_health_history,
    get_endpoint_health_summary_since,
    get_endpoint_uptime_pct_since,
    get_installation,
    get_installation_by_account_login,
    get_latest_evidence,
    get_public_status_enabled,
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
from app_server.github_pagination import fetch_paginated_github_collection

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


def _fetch_uninitialized_repos_sync(installation_id: int, app_jwt: str) -> list[dict]:
    token = get_installation_token(installation_id, app_jwt)
    return fetch_paginated_github_collection(
        _github_http_client(),
        "/installation/repositories",
        headers={
            "Authorization": f"Bearer {token}",
            "Accept": "application/vnd.github+json",
        },
        collection_key="repositories",
    )


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
        repositories = await asyncio.to_thread(
            _fetch_uninitialized_repos_sync, installation_id, app_jwt
        )
    except Exception:
        return []

    result = []
    for repo in repositories:
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
        # capability AIR itself sells. The flash plan doesn't get a
        # managed dashboard either - same self-service-only shape as
        # free, just with paid PR-review limits.
        if row["plan"] != "air":
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
        # AIR-exclusive - no managed dashboard for flash either.
        if installation is None or installation["plan"] != "air":
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


async def _require_dashboard_installation(request: Request, org: str, repo: str) -> tuple[dict, int]:
    # Session first - an unauthenticated caller learns nothing. The repo
    # lookup below still has to run before ownership can be checked against
    # it (_repo_installation_id is what resolves org/repo to an
    # installation_id in the first place); what's closed is the response
    # itself: a real repo the caller doesn't administer and a repo that's
    # never been connected at all now get the identical 404, so an
    # authenticated-but-unauthorized caller can't use the status code to
    # learn which org/repos this product has ever seen
    # (docs/audits/Claude_Audit.md finding 34).
    session = await get_current_session(request)
    if session is None:
        raise HTTPException(status_code=401, detail="login required")

    pool = request.app.state.db_pool
    installation_id = await _repo_installation_id(pool, org, repo)

    administered_ids = await _administered_installation_ids_for_session_or_401(pool, session)
    if installation_id is None or installation_id not in administered_ids:
        raise HTTPException(status_code=404, detail="no such repo")

    installation = await get_installation(pool, installation_id)
    if installation is not None:
        # AIR-exclusive - no managed dashboard for flash either.
        if installation["plan"] != "air":
            raise HTTPException(status_code=402, detail="the managed dashboard requires the AIR plan")
        await _require_seat_if_paid(pool, installation, session["github_login"], f"{org}/{repo}")

    return session, installation_id


@dashboard_router.get("/app/{org}/{repo}")
async def get_dashboard(org: str, repo: str, request: Request):
    _session, installation_id = await _require_dashboard_installation(request, org, repo)
    pool = request.app.state.db_pool
    repo_full_name = f"{org}/{repo}"
    history = await get_recent_history(pool, installation_id, repo_full_name)
    dismissed = await get_dismissed_identity_keys(pool, installation_id, repo_full_name)
    return {
        "repo_full_name": repo_full_name,
        "history": history,
        "dismissed_finding_keys": {
            "secret": list(dismissed["secret"]),
            "vulnerability": list(dismissed["vulnerability"]),
        },
    }


@dashboard_router.post("/app/{org}/{repo}/findings/dismiss")
async def dismiss_finding_route(org: str, repo: str, request: Request):
    session, installation_id = await _require_dashboard_installation(request, org, repo)
    body = await request.json()
    finding_type = body.get("finding_type")
    finding = body.get("finding")
    if finding_type not in ("secret", "vulnerability") or not isinstance(finding, dict):
        raise HTTPException(status_code=400, detail="invalid finding_type or finding")

    pool = request.app.state.db_pool
    repo_full_name = f"{org}/{repo}"
    try:
        await dismiss_finding(
            pool, installation_id, repo_full_name, finding_type, finding,
            session["github_login"], body.get("reason"),
        )
    except KeyError as exc:
        raise HTTPException(status_code=400, detail=f"finding missing required field: {exc}") from exc
    return {"ok": True}


@dashboard_router.post("/app/{org}/{repo}/findings/undismiss")
async def undismiss_finding_route(org: str, repo: str, request: Request):
    _session, installation_id = await _require_dashboard_installation(request, org, repo)
    body = await request.json()
    finding_type = body.get("finding_type")
    finding = body.get("finding")
    if finding_type not in ("secret", "vulnerability") or not isinstance(finding, dict):
        raise HTTPException(status_code=400, detail="invalid finding_type or finding")

    pool = request.app.state.db_pool
    repo_full_name = f"{org}/{repo}"
    try:
        await undismiss_finding(pool, installation_id, repo_full_name, finding_type, finding)
    except KeyError as exc:
        raise HTTPException(status_code=400, detail=f"finding missing required field: {exc}") from exc
    return {"ok": True}


@dashboard_router.get("/app/{org}/{repo}/health")
async def get_dashboard_health(org: str, repo: str, request: Request):
    _session, installation_id = await _require_dashboard_installation(request, org, repo)
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
    _session, installation_id = await _require_dashboard_installation(request, org, repo)
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

    evidence = await get_latest_evidence(pool, installation["installation_id"], repo_full_name)
    if evidence is not None:
        for file_entry in subsystem.get("files", []) or []:
            if not isinstance(file_entry, dict) or file_entry.get("detail"):
                continue
            fallback = build_file_fallback_detail(
                evidence, file_entry.get("path", ""), file_entry=file_entry
            )
            if fallback:
                file_entry["detail"] = fallback
                file_entry["detail_source"] = "fallback"

    subsystem["updated_at"] = subsystem["updated_at"].isoformat()
    return {"repo_full_name": repo_full_name, "subsystem": subsystem}


def _valid_wiki_file_path(path: str) -> bool:
    parts = path.split("/")
    return bool(path and not path.startswith("/") and ".." not in parts)


def _fetch_wiki_file_content_sync(
    installation_id: int, repo_full_name: str, path: str
) -> str | None:
    settings = get_settings()
    app_jwt = generate_app_jwt(settings.github_app_id, settings.github_app_private_key)
    token = get_installation_token(installation_id, app_jwt)
    return fetch_file_content(_github_http_client(), token, repo_full_name, path)


@dashboard_router.get("/app/{org}/{repo}/wiki/file/{file_path:path}")
async def get_dashboard_wiki_file(org: str, repo: str, file_path: str, request: Request):
    """Returns a generated file page or a cheap structural fallback.

    AIRview intentionally writes pages for only the most important files.
    Arbitrary-file reads must still be useful, so scanned modules use their
    symbols and dependency graph immediately, while files outside the scan
    (docs/config/workflow files) get one bounded GitHub Contents lookup.
    Neither path invokes an LLM or changes the full-build page budget.
    """
    if not _valid_wiki_file_path(file_path):
        raise HTTPException(status_code=400, detail="invalid file path")

    installation = await _require_admin_installation(request, org, repo)
    pool = request.app.state.db_pool
    installation_id = installation["installation_id"]
    repo_full_name = f"{org}/{repo}"
    evidence = await get_latest_evidence(pool, installation_id, repo_full_name)
    if evidence is None:
        raise HTTPException(status_code=404, detail="no scan evidence")

    file_entry = None
    for subsystem in await list_wiki_subsystems(pool, installation_id, repo_full_name):
        for candidate in subsystem.get("files", []) or []:
            if isinstance(candidate, dict) and candidate.get("path") == file_path:
                file_entry = candidate
                break
        if file_entry is not None:
            break

    if file_entry and file_entry.get("detail"):
        return {
            "repo_full_name": repo_full_name,
            "file": {"path": file_path, "detail": file_entry["detail"], "detail_source": "generated"},
        }

    modules = evidence.get("repository", {}).get("modules", [])
    module_exists = any(m.get("path") == file_path for m in modules)
    source_text = None
    if not module_exists:
        try:
            source_text = await asyncio.to_thread(
                _fetch_wiki_file_content_sync, installation_id, repo_full_name, file_path
            )
        except Exception as exc:  # noqa: BLE001
            logging.getLogger(__name__).info(
                "AIRview file fallback fetch failed for %s (%s)", file_path, type(exc).__name__
            )

    detail = build_file_fallback_detail(
        evidence, file_path, file_entry=file_entry, source_text=source_text
    )
    if detail is None:
        raise HTTPException(status_code=404, detail="file not found in scan or repository")
    return {
        "repo_full_name": repo_full_name,
        "file": {"path": file_path, "detail": detail, "detail_source": "fallback"},
    }


async def _build_docs_modules(pool, installation_id: int, repo_full_name: str) -> dict[str, str]:
    """Shared by the JSON dashboard route and the markdown export route -
    both render the same evidence + AI-description merge, just packaged
    differently."""
    from aletheore.docs_reference import build_api_reference

    evidence = await get_latest_evidence(pool, installation_id, repo_full_name)
    if evidence is None:
        return {}

    symbols = await list_docs_symbols(pool, installation_id, repo_full_name)
    ai_descriptions_by_module: dict[str, dict[str, dict]] = {}
    for row in symbols:
        ai_descriptions_by_module.setdefault(row["module_path"], {})[row["symbol_name"]] = {
            "description": row["description"],
            "mode": row["mode"],
        }
    return build_api_reference(evidence, ai_descriptions_by_module)


@dashboard_router.get("/app/{org}/{repo}/docs")
async def get_dashboard_docs(org: str, repo: str, request: Request):
    """Grounded API reference (docs_reference.py) merged with whatever
    AI-generated/polished descriptions live_docs.py has stored, exactly
    the same way the pure-evidence CLI/query path renders it - this route
    is the only place that gets to use ai_descriptions_by_module, since
    it's the paid-plan-gated one (_require_admin_installation's own
    "plan == free" -> 402 already covers this, same as /wiki).
    """
    installation = await _require_admin_installation(request, org, repo)
    pool = request.app.state.db_pool
    installation_id = installation["installation_id"]
    repo_full_name = f"{org}/{repo}"

    build_status = await get_docs_build_status(pool, installation_id, repo_full_name)
    modules = await _build_docs_modules(pool, installation_id, repo_full_name)
    return {
        "repo_full_name": repo_full_name,
        "modules": modules,
        "build_status": build_status["status"] if build_status is not None else None,
        "build_error": build_status["error_message"] if build_status is not None else None,
    }


_UNSAFE_FILENAME_CHARS_RE = re.compile(r'[^A-Za-z0-9._-]')


def _safe_download_filename(name: str) -> str:
    """`repo` here is a raw URL path segment - _repo_installation_id only
    ever matches it against the org's account_login, never validates it
    against a real, existing repo name - so unlike every other route that
    just uses org/repo for a DB lookup or JSON field, embedding it directly
    into a response header (as this route's filename does) would carry
    whatever characters an authenticated admin's browser happened to send,
    including a stray '"' that breaks the Content-Disposition value's
    quoting. Stripped down to a safe subset rather than rejected outright,
    since a mangled-but-safe filename is a better failure mode here than a
    500 on an otherwise-valid request."""
    safe = _UNSAFE_FILENAME_CHARS_RE.sub("_", name)
    return safe or "repo"


@dashboard_router.get("/app/{org}/{repo}/docs/export")
async def get_dashboard_docs_export(org: str, repo: str, request: Request):
    """The same grounded API reference as get_dashboard_docs, combined into
    one downloadable markdown document instead of per-module dashboard
    cards - for anyone who wants the whole reference in one file to grep,
    diff, or paste elsewhere instead of opening each module's accordion."""
    from aletheore.docs_reference import build_combined_reference

    installation = await _require_admin_installation(request, org, repo)
    pool = request.app.state.db_pool
    installation_id = installation["installation_id"]
    repo_full_name = f"{org}/{repo}"

    modules = await _build_docs_modules(pool, installation_id, repo_full_name)
    markdown = build_combined_reference(modules, repo_full_name)
    filename = f"{_safe_download_filename(repo)}-api-reference.md"
    return Response(
        content=markdown,
        media_type="text/markdown",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


PUBLIC_HEALTH_RATE_LIMIT = 60
PUBLIC_HEALTH_RATE_LIMIT_WINDOW_SECONDS = 60

# The sweep re-checks every endpoint still present in the latest scan every
# ~3 minutes (scan_worker.scheduler.HEALTH_SWEEP_INTERVAL_SECONDS). An
# endpoint that stops being checked - because the route was removed, or
# because it was never a real route to begin with (a scanner false
# positive that later got fixed) - simply stops getting new rows, but its
# last-ever row would otherwise live in this DISTINCT ON query forever.
# Filtering to recently-checked rows lets stale/removed endpoints age out
# of this public, unauthenticated API on their own instead of being
# reported as "up" indefinitely after they stop existing.
PUBLIC_HEALTH_STALE_AFTER = timedelta(minutes=15)


@dashboard_router.get("/v1/health/{org}/{repo}")
async def get_public_health(org: str, repo: str, request: Request, response: Response):
    response.headers["Access-Control-Allow-Origin"] = "*"

    from app_server.paddle_ip_allowlist import client_ip_from_forwarded_for
    from app_server.rate_limit import is_rate_limited
    from app_server.redis_client import get_redis_client

    client_ip = client_ip_from_forwarded_for(
        request.headers.get("x-forwarded-for"),
        request.client.host if request.client else "",
    )
    try:
        # is_rate_limited uses the synchronous redis-py client and blocks on
        # pipe.execute() - run off the event loop (asyncio.to_thread, same
        # pattern as embeddings_api.py's #328 fix). This route is public and
        # unauthenticated, so it's the most exposed instance of this gap:
        # without this, a burst of requests here stalls the event loop for
        # every other concurrent request on this worker, not just this one.
        rate_limited = await asyncio.to_thread(
            is_rate_limited,
            get_redis_client(),
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

    # Off by default (migration 043) - endpoint paths, reachability, and
    # latency derived from a customer's private repository must not be
    # exposed to anyone who knows the org/repo without an explicit
    # opt-in. Same 404 shape as "no health data" below, rather than a
    # distinct status, so this doesn't itself disclose whether the repo
    # has simply never opted in vs never been scanned.
    installation = await get_installation_by_account_login(request.app.state.db_pool, org)
    if installation is None or not await get_public_status_enabled(
        request.app.state.db_pool, installation["installation_id"], repo_full_name
    ):
        raise HTTPException(
            status_code=404,
            detail="no health data for this repo",
            headers={"Access-Control-Allow-Origin": "*"},
        )

    rows = await request.app.state.db_pool.fetch(
        """
        SELECT DISTINCT ON (endpoint_method, endpoint_path)
            endpoint_method, endpoint_path, reachable, status_code, latency_ms, checked_at
        FROM endpoint_health
        WHERE installation_id = $1 AND repo_full_name = $2 AND checked_at >= $3
        ORDER BY endpoint_method, endpoint_path, checked_at DESC, id DESC
        """,
        installation["installation_id"],
        repo_full_name,
        datetime.now(timezone.utc) - PUBLIC_HEALTH_STALE_AFTER,
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
    uptime_by_endpoint = await get_endpoint_uptime_pct_since(
        request.app.state.db_pool, installation["installation_id"], repo_full_name, since
    )

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
