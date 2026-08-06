import logging

from app_server.config import get_settings
from app_server.github_auth import generate_app_jwt, get_installation_token, get_repo_permission_for_user

logger = logging.getLogger(__name__)

AUDIT_COMMAND = "/aletheore audit"

# Anyone who can push to the repo can already do everything a managed audit
# does (read the code, spend the org's own compute) - "read" or below is
# exactly the set of people an outside PR commenter represents, which is
# who this check exists to stop.
AUTHORIZED_PERMISSIONS = ("admin", "write")


async def handle_issue_comment_event(payload: dict, redis_url: str, queue=None) -> None:
    if payload.get("action") != "created":
        return
    if "pull_request" not in payload.get("issue", {}):
        return
    if AUDIT_COMMAND not in payload.get("comment", {}).get("body", ""):
        return

    installation_id = payload["installation"]["id"]
    repo_full_name = payload["repository"]["full_name"]
    commenter = payload["comment"]["user"]["login"]

    settings = get_settings()
    try:
        app_jwt = generate_app_jwt(settings.github_app_id, settings.github_app_private_key)
        token = await get_installation_token(installation_id, app_jwt)
        permission = get_repo_permission_for_user(repo_full_name, commenter, token)
    except Exception:
        # Fail closed: an API hiccup here should silently drop a legitimate
        # trigger (the maintainer can just comment again), not let an
        # unverified commenter through because the check itself errored.
        logger.warning(
            "failed to verify commenter permission for %s on %s; refusing to enqueue audit",
            commenter,
            repo_full_name,
            exc_info=True,
        )
        return

    if permission not in AUTHORIZED_PERMISSIONS:
        logger.info(
            "ignoring '%s' from %s on %s: permission=%r, need write or admin",
            AUDIT_COMMAND,
            commenter,
            repo_full_name,
            permission,
        )
        return

    if queue is None:
        from redis import Redis
        from rq import Queue

        queue = Queue("scans", connection=Redis.from_url(redis_url))

    queue.enqueue(
        "scan_worker.jobs.run_managed_audit_pr_job",
        job_timeout=900,
        installation_id=installation_id,
        repo_full_name=repo_full_name,
        pr_number=payload["issue"]["number"],
    )
