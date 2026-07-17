from fastapi import APIRouter, HTTPException, Request, Response

from app_server.db import get_recent_history

dashboard_router = APIRouter()


@dashboard_router.get("/app/{org}/{repo}")
async def get_dashboard(org: str, repo: str, request: Request):
    repo_full_name = f"{org}/{repo}"
    pool = request.app.state.db_pool
    row = await pool.fetchrow(
        """
        SELECT DISTINCT installation_id
        FROM repo_history
        WHERE repo_full_name = $1
        LIMIT 1
        """,
        repo_full_name,
    )
    if row is None:
        raise HTTPException(status_code=404, detail="no scan history for this repo")

    history = await get_recent_history(pool, row["installation_id"], repo_full_name)
    return {"repo_full_name": repo_full_name, "history": history}


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
