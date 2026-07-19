AUDIT_COMMAND = "/aletheore audit"


async def handle_issue_comment_event(payload: dict, redis_url: str, queue=None) -> None:
    if payload.get("action") != "created":
        return
    if "pull_request" not in payload.get("issue", {}):
        return
    if AUDIT_COMMAND not in payload.get("comment", {}).get("body", ""):
        return

    if queue is None:
        from redis import Redis
        from rq import Queue

        queue = Queue("scans", connection=Redis.from_url(redis_url))

    queue.enqueue(
        "scan_worker.jobs.run_managed_audit_pr_job",
        job_timeout=900,
        installation_id=payload["installation"]["id"],
        repo_full_name=payload["repository"]["full_name"],
        pr_number=payload["issue"]["number"],
    )
