async def handle_push_event(payload: dict, redis_url: str, queue=None) -> None:
    # Every branch/tag push fires this event - only a push that actually
    # lands on the repository's default branch is "what's on main" for
    # AIRview's purposes. Branch deletions carry after == "0000...0" and
    # have nothing to scan.
    if payload.get("deleted"):
        return

    ref = payload.get("ref", "")
    default_branch = payload.get("repository", {}).get("default_branch", "")
    if ref != f"refs/heads/{default_branch}":
        return

    changed_files: set[str] = set()
    for commit in payload.get("commits", []):
        changed_files.update(commit.get("added", []))
        changed_files.update(commit.get("removed", []))
        changed_files.update(commit.get("modified", []))

    if queue is None:
        from redis import Redis
        from rq import Queue

        queue = Queue("scans", connection=Redis.from_url(redis_url))

    queue.enqueue(
        "scan_worker.jobs.run_push_scan_job",
        job_timeout=300,
        installation_id=payload["installation"]["id"],
        repo_full_name=payload["repository"]["full_name"],
        head_sha=payload["after"],
        changed_files=sorted(changed_files),
    )
