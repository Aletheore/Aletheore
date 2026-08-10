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
DEMO_SCAN_MAX_QUEUE_DEPTH = 4  # queued + running, ahead of the single demo-scan-worker
DEMO_SCAN_JOB_TIMEOUT_SECONDS = 120
DEMO_SCAN_RESULT_TTL_SECONDS = 900


class StartDemoScanRequest(BaseModel):
    repo_url: str


def _client_ip(request: Request) -> str:
    from app_server.paddle_ip_allowlist import client_ip_from_forwarded_for

    return client_ip_from_forwarded_for(
        request.headers.get("x-forwarded-for"),
        request.client.host if request.client else "unknown",
    )


def _get_queue(redis_url: str):
    from rq import Queue

    from app_server.redis_client import get_redis_client

    return Queue(DEMO_SCAN_QUEUE_NAME, connection=get_redis_client())


def _fetch_job(job_id: str, redis_url: str):
    from rq.job import Job

    from app_server.redis_client import get_redis_client

    return Job.fetch(job_id, connection=get_redis_client())


_GENERIC_FAILURE_DETAIL = "scan failed - the repo may be invalid, private, or too large"


def _failure_detail(exc_info: str | None) -> str:
    # RQ only stores the failure as a formatted traceback string, no
    # structured exception object - but that string's own last non-empty
    # line is always "<ExceptionClassName>: <message>" (Python's own
    # traceback format), so this reliably recovers just the message for our
    # own deliberately-raised, user-facing DemoScanError. Anything else
    # (a real bug, an unexpected exception type) falls back to the generic
    # message rather than ever showing internal details to a public visitor.
    if not exc_info:
        return _GENERIC_FAILURE_DETAIL
    lines = [line for line in exc_info.strip().splitlines() if line.strip()]
    if not lines:
        return _GENERIC_FAILURE_DETAIL
    last_line = lines[-1]
    prefix = "scan_worker.demo_scan.DemoScanError: "
    if last_line.startswith(prefix):
        return last_line[len(prefix):]
    if last_line.startswith("DemoScanError: "):
        return last_line[len("DemoScanError: "):]
    return _GENERIC_FAILURE_DETAIL


async def _check_repo_size(owner: str, repo: str, token: str | None) -> None:
    headers = {"Accept": "application/vnd.github+json"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    try:
        async with httpx.AsyncClient() as client:
            response = await client.get(
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
    await _check_repo_size(owner, repo, settings.github_demo_readonly_token)

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
        return {"status": "failed", "detail": _failure_detail(job.exc_info)}
    if job.is_finished:
        return {"status": "finished", "result": job.result}

    status = job.get_status()
    if status == "queued":
        # Only one worker consumes this queue (see demo_scan_worker.py), so
        # without this a visitor waiting behind someone else's scan sees the
        # same "scanning" message as the person actually being scanned, with
        # no way to tell they're just waiting in line.
        queue = _get_queue(settings.redis_url)
        return {"status": "queued", "queue_position": queue.get_job_position(job.id)}
    return {"status": status}
