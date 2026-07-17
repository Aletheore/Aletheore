import hashlib

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import JSONResponse

from app_server.config import get_settings
from app_server.db import get_installation_by_token_hash, touch_api_token

managed_audit_router = APIRouter()


def _get_queue(redis_url: str):
    from redis import Redis
    from rq import Queue

    return Queue("scans", connection=Redis.from_url(redis_url))


def _fetch_job(job_id: str, redis_url: str):
    from redis import Redis
    from rq.job import Job

    return Job.fetch(job_id, connection=Redis.from_url(redis_url))


@managed_audit_router.post("/v1/managed-audit")
async def start_managed_audit(request: Request):
    auth_header = request.headers.get("Authorization", "")
    if not auth_header.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="missing bearer token")

    raw_token = auth_header.removeprefix("Bearer ")
    token_hash = hashlib.sha256(raw_token.encode()).hexdigest()
    pool = request.app.state.db_pool
    installation = await get_installation_by_token_hash(pool, token_hash)
    if installation is None:
        raise HTTPException(status_code=401, detail="invalid or revoked token")
    if installation["plan"] == "free":
        raise HTTPException(status_code=402, detail="managed audits require a paid plan")

    await touch_api_token(pool, token_hash)
    evidence = (await request.json())["evidence"]
    job = _get_queue(get_settings().redis_url).enqueue(
        "scan_worker.jobs.run_managed_audit_api_job",
        evidence=evidence,
    )
    return JSONResponse(status_code=202, content={"job_id": job.id})


@managed_audit_router.get("/v1/managed-audit/{job_id}")
async def get_managed_audit_status(job_id: str):
    job = _fetch_job(job_id, get_settings().redis_url)
    if job.is_failed:
        return {"status": "failed"}
    if job.is_finished:
        return {"status": "finished", "result": job.result}
    return {"status": "pending"}
