ENQUEUE_ACTIONS = ("opened", "reopened", "synchronize")

PR_SCAN_JOB_TIMEOUT_SECONDS = 300
# Flash review makes a real LLM call plus several sequential GitHub API
# fetches (diff, changed files, per-symbol source) - a real run against a
# ~10-file diff measured at 5m50s in production. The previous 180s value
# was silently killing most non-trivial reviews: RQ's work-horse watchdog
# SIGKILLs the job at job_timeout+60s (here, 240s), which is a hard kill
# the job's own except block never sees, so no failure comment gets
# posted and nothing gets logged - it just vanishes. Sized well above the
# observed worst case, not the common case.
FLASH_REVIEW_JOB_TIMEOUT_SECONDS = 900


async def handle_pull_request_event(payload: dict, redis_url: str, queue=None) -> None:
    if payload.get("action") not in ENQUEUE_ACTIONS:
        return

    if queue is None:
        from redis import Redis
        from rq import Queue

        queue = Queue("scans", connection=Redis.from_url(redis_url))

    queue.enqueue(
        "scan_worker.jobs.run_pr_scan_job",
        job_timeout=PR_SCAN_JOB_TIMEOUT_SECONDS,
        installation_id=payload["installation"]["id"],
        repo_full_name=payload["repository"]["full_name"],
        pr_number=payload["number"],
        base_sha=payload["pull_request"]["base"]["sha"],
        head_sha=payload["pull_request"]["head"]["sha"],
    )
    queue.enqueue(
        "scan_worker.jobs.run_flash_review_job",
        job_timeout=FLASH_REVIEW_JOB_TIMEOUT_SECONDS,
        installation_id=payload["installation"]["id"],
        repo_full_name=payload["repository"]["full_name"],
        pr_number=payload["number"],
        base_sha=payload["pull_request"]["base"]["sha"],
        head_sha=payload["pull_request"]["head"]["sha"],
    )
