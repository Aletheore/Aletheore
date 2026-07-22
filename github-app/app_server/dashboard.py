from fastapi import APIRouter, HTTPException, Request, Response

from aletheore.evidence_resolution import resolve_code_evidence
from app_server.admin import (
    _administered_installation_ids,
    _repo_installation_id,
    _require_admin_installation,
)
from app_server.auth import get_current_session
from app_server.db import (
    get_latest_evidence,
    get_recent_endpoint_health,
    get_recent_history,
    get_wiki_overview,
    get_wiki_subsystem,
    list_repos_for_installations,
    list_wiki_subsystems,
)

dashboard_router = APIRouter()


@dashboard_router.get("/app/repos")
async def list_my_repos(request: Request):
    session = await get_current_session(request)
    if session is None:
        raise HTTPException(status_code=401, detail="login required")

    administered_ids = await _administered_installation_ids(session["github_access_token"])
    pool = request.app.state.db_pool
    repos = await list_repos_for_installations(pool, list(administered_ids))
    result = []
    for row in repos:
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
            }
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

    administered_ids = await _administered_installation_ids(session["github_access_token"])
    if installation_id not in administered_ids:
        raise HTTPException(status_code=403, detail="you do not administer this installation")

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

    return {"repo_full_name": repo_full_name, "endpoints": endpoints}


@dashboard_router.get("/app/{org}/{repo}/wiki")
async def get_dashboard_wiki(org: str, repo: str, request: Request):
    installation = await _require_admin_installation(request, org, repo)
    pool = request.app.state.db_pool
    installation_id = installation["installation_id"]
    repo_full_name = f"{org}/{repo}"

    overview = await get_wiki_overview(pool, installation_id, repo_full_name)
    if overview is not None:
        overview["updated_at"] = overview["updated_at"].isoformat()

    subsystems = await list_wiki_subsystems(pool, installation_id, repo_full_name)
    return {
        "repo_full_name": repo_full_name,
        "overview": overview,
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


@dashboard_router.get("/v1/health/{org}/{repo}")
async def get_public_health(org: str, repo: str, request: Request, response: Response):
    response.headers["Access-Control-Allow-Origin"] = "*"
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
            }
            for row in rows
        ],
    }
