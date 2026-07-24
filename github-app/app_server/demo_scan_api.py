"""Public, unauthenticated "paste a repo" live demo on the marketing
website. Visitors get the deterministic scan only (dead code, secrets
existence, license issues, endpoint mapping) - no LLM calls, no OSV.dev
lookup, and the cloned source is never persisted (see scan_worker.demo_scan).
"""

import logging

import httpx
from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from app_server.config import get_settings
from app_server.db import check_and_reserve_demo_scan
from app_server.demo_scan_validation import MAX_REPO_SIZE_KB, normalized_clone_url, parse_github_repo_url

demo_scan_router = APIRouter()
logger = logging.getLogger(__name__)

DEMO_SCAN_QUEUE_NAME = "demo_scan"
DEMO_SCAN_JOB_FUNCTION = "scan_worker.demo_scan.run_demo_scan_job"
DEMO_SCAN_COOLDOWN_SECONDS = 20 * 60  # ~3 runs/hour per visitor
DEMO_SCAN_MAX_QUEUE_DEPTH = 4  # queued + running, matches 2 dedicated workers
DEMO_SCAN_JOB_TIMEOUT_SECONDS = 120
DEMO_SCAN_RESULT_TTL_SECONDS = 900


class StartDemoScanRequest(BaseModel):
    repo_url: str


def _client_ip(request: Request) -> str:
    # app-server is only reachable via Caddy's reverse_proxy inside the
    # docker network (bound to 127.0.0.1:8000 on the host) - Caddy is the
    # only thing that can set this header, so trusting it here is safe.
    forwarded_for = request.headers.get("x-forwarded-for")
    if forwarded_for:
        return forwarded_for.split(",")[0].strip()
    return request.client.host if request.client else "unknown"


def _get_queue(redis_url: str):
    from redis import Redis
    from rq import Queue

    return Queue(DEMO_SCAN_QUEUE_NAME, connection=Redis.from_url(redis_url))


def _fetch_job(job_id: str, redis_url: str):
    from redis import Redis
    from rq.job import Job

    return Job.fetch(job_id, connection=Redis.from_url(redis_url))


def _check_repo_size(owner: str, repo: str, token: str | None) -> None:
    headers = {"Accept": "application/vnd.github+json"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    try:
        response = httpx.get(
            f"https://api.github.com/repos/{owner}/{repo}", headers=headers, timeout=10.0
        )
    except httpx.HTTPError as exc:
        raise HTTPException(status_code=502, detail="could not reach GitHub - try again shortly") from exc

    if response.status_code == 404:
        raise HTTPException(status_code=404, detail="repo not found - must be a public GitHub repo")
    if response.status_code == 403:
        # Almost certainly our own GitHub API rate limit, not the caller's fault.
        logger.warning("GitHub API returned 403 checking repo size for %s/%s", owner, repo)
        raise HTTPException(status_code=503, detail="demo is busy right now - try again shortly")
    response.raise_for_status()

    size_kb = response.json().get("size", 0)
    if size_kb > MAX_REPO_SIZE_KB:
        raise HTTPException(
            status_code=413,
            detail=(
                f"repo is too large for the live demo ({size_kb // 1024}MB, limit "
                f"{MAX_REPO_SIZE_KB // 1024}MB) - install the Aletheore CLI and run "
                "`aletheore scan` locally, or connect via MCP, to see the full report"
            ),
        )


@demo_scan_router.post("/v1/demo-scan")
async def start_demo_scan(request: Request, body: StartDemoScanRequest):
    parsed = parse_github_repo_url(body.repo_url)
    if parsed is None:
        raise HTTPException(
            status_code=400,
            detail="must be a public GitHub repo URL, e.g. https://github.com/owner/repo",
        )
    owner, repo = parsed

    settings = get_settings()
    pool = request.app.state.db_pool

    # Size (and existence) is checked before the rate-limit slot is
    # reserved - a repo that gets rejected here never reaches a worker, so
    # it shouldn't cost the visitor their one scan every 20 minutes. Only a
    # request that's actually eligible to run consumes the cooldown.
    _check_repo_size(owner, repo, settings.github_demo_readonly_token)

    allowed = await check_and_reserve_demo_scan(pool, _client_ip(request), DEMO_SCAN_COOLDOWN_SECONDS)
    if not allowed:
        raise HTTPException(
            status_code=429,
            detail=(
                f"one live demo every {DEMO_SCAN_COOLDOWN_SECONDS // 60} minutes per visitor - "
                "try again shortly"
            ),
        )

    queue = _get_queue(settings.redis_url)
    in_flight = queue.count + queue.started_job_registry.count
    if in_flight >= DEMO_SCAN_MAX_QUEUE_DEPTH:
        raise HTTPException(status_code=503, detail="demo is busy right now - try again shortly")

    job = queue.enqueue(
        DEMO_SCAN_JOB_FUNCTION,
        job_timeout=DEMO_SCAN_JOB_TIMEOUT_SECONDS,
        result_ttl=DEMO_SCAN_RESULT_TTL_SECONDS,
        repo_url=normalized_clone_url(owner, repo),
    )
    return JSONResponse(status_code=202, content={"job_id": job.id})


@demo_scan_router.get("/v1/demo-scan/{job_id}")
async def get_demo_scan_status(job_id: str):
    from rq.exceptions import NoSuchJobError

    settings = get_settings()
    try:
        job = _fetch_job(job_id, settings.redis_url)
    except NoSuchJobError as exc:
        raise HTTPException(status_code=404, detail="job not found") from exc

    # This Redis instance backs every RQ queue in the app, not just demo
    # scans - without this check, a public unauthenticated endpoint could be
    # used to probe the status/result of unrelated jobs by guessing job ids.
    if job.origin != DEMO_SCAN_QUEUE_NAME or job.func_name != DEMO_SCAN_JOB_FUNCTION:
        raise HTTPException(status_code=404, detail="job not found")

    if job.is_failed:
        return {"status": "failed", "detail": "scan failed - the repo may be invalid, private, or too large"}
    if job.is_finished:
        return {"status": "finished", "result": job.result}
    return {"status": job.get_status()}
