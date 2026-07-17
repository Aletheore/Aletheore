from fastapi import APIRouter, HTTPException, Request

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
