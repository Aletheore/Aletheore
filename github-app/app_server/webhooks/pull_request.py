ENQUEUE_ACTIONS = ("opened", "reopened", "synchronize")


async def handle_pull_request_event(payload: dict, redis_url: str, queue=None) -> None:
    if payload.get("action") not in ENQUEUE_ACTIONS:
        return

    if queue is None:
        from redis import Redis
        from rq import Queue

        queue = Queue("scans", connection=Redis.from_url(redis_url))

    queue.enqueue(
        "scan_worker.jobs.run_pr_scan_job",
        installation_id=payload["installation"]["id"],
        repo_full_name=payload["repository"]["full_name"],
        pr_number=payload["number"],
        base_sha=payload["pull_request"]["base"]["sha"],
        head_sha=payload["pull_request"]["head"]["sha"],
    )
    queue.enqueue(
        "scan_worker.jobs.run_flash_review_job",
        installation_id=payload["installation"]["id"],
        repo_full_name=payload["repository"]["full_name"],
        pr_number=payload["number"],
        base_sha=payload["pull_request"]["base"]["sha"],
        head_sha=payload["pull_request"]["head"]["sha"],
    )
