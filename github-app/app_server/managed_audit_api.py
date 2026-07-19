import hashlib

import toon
from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import JSONResponse

from app_server.config import get_settings
from app_server.db import check_and_reserve_managed_audit, get_installation_by_token_hash, touch_api_token
from app_server.rate_limit import cooldown_seconds_for_loc, total_loc_from_evidence

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

    body = await request.json()
    repo_full_name = body.get("repo_full_name")
    if not repo_full_name:
        raise HTTPException(status_code=400, detail="repo_full_name is required")

    evidence = body["evidence"]
    try:
        decoded_evidence = toon.decode(evidence)
    except Exception as exc:
        raise HTTPException(status_code=400, detail="evidence could not be decoded") from exc

    cooldown_seconds = cooldown_seconds_for_loc(total_loc_from_evidence(decoded_evidence))
    allowed = await check_and_reserve_managed_audit(
        pool, installation["installation_id"], repo_full_name, cooldown_seconds
    )
    if not allowed:
        raise HTTPException(
            status_code=429,
            detail=(
                f"managed audit rate limit: this repo can run one managed audit every "
                f"{cooldown_seconds // 3600} hours - try again later"
            ),
        )

    await touch_api_token(pool, token_hash)
    job = _get_queue(get_settings().redis_url).enqueue(
        "scan_worker.jobs.run_managed_audit_api_job",
        job_timeout=900,
        installation_id=installation["installation_id"],
        evidence=evidence,
    )
    return JSONResponse(status_code=202, content={"job_id": job.id})


@managed_audit_router.get("/v1/whoami")
async def whoami(request: Request):
    auth_header = request.headers.get("Authorization", "")
    if not auth_header.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="missing bearer token")

    raw_token = auth_header.removeprefix("Bearer ")
    token_hash = hashlib.sha256(raw_token.encode()).hexdigest()
    installation = await get_installation_by_token_hash(request.app.state.db_pool, token_hash)
    if installation is None:
        raise HTTPException(status_code=401, detail="invalid or revoked token")
    return {"account_login": installation["account_login"], "plan": installation["plan"]}


@managed_audit_router.get("/v1/managed-audit/{job_id}")
async def get_managed_audit_status(job_id: str):
    job = _fetch_job(job_id, get_settings().redis_url)
    if job.is_failed:
        return {"status": "failed"}
    if job.is_finished:
        return {"status": "finished", "result": job.result}
    return {"status": "pending"}
