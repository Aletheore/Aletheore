import asyncio
import logging

from app_server.config import get_settings
from app_server.db import get_installation, is_repo_hidden
from app_server.github_auth import generate_app_jwt, get_installation_token, get_repo_permission_for_user

logger = logging.getLogger(__name__)

AUDIT_COMMAND = "/aletheore audit"

# Anyone who can push to the repo can already do everything a managed audit
# does (read the code, spend the org's own compute) - "read" or below is
# exactly the set of people an outside PR commenter represents, which is
# who this check exists to stop.
AUTHORIZED_PERMISSIONS = ("admin", "write")


def _verify_commenter_permission_sync(
    installation_id: int, app_jwt: str, repo_full_name: str, commenter: str
) -> str:
    token = get_installation_token(installation_id, app_jwt)
    return get_repo_permission_for_user(repo_full_name, commenter, token)


async def handle_issue_comment_event(payload: dict, pool, redis_url: str, queue=None) -> None:
    if payload.get("action") != "created":
        return
    if "pull_request" not in payload.get("issue", {}):
        return
    comment = payload.get("comment", {})
    body = comment.get("body", "")
    if not any(line.strip().startswith(AUDIT_COMMAND) for line in body.splitlines()):
        return
    if comment.get("user", {}).get("type") == "Bot":
        return

    installation_id = payload["installation"]["id"]
    repo_full_name = payload["repository"]["full_name"]
    commenter = payload["comment"]["user"]["login"]

    # Managed audits are a paid feature (see managed_audit_api.py's own
    # 402 for the HTTP trigger) - this ChatOps trigger had no equivalent
    # gate at all, letting any repo with write/admin access (trivially
    # granted by installing the free app on your own repo) run unlimited
    # clone+scan cycles on the shared scans queue. Silent, matching the
    # permission-denied case right below: this fires from any commenter
    # with write access, not just the installer, so it's not obviously a
    # billing question they're asking - same reasoning as staying quiet
    # on a permission check failure rather than narrating access details
    # to whoever happens to comment.
    installation = await get_installation(pool, installation_id)
    if installation is None or installation["plan"] == "free":
        logger.info(
            "ignoring '%s' on %s: managed audits require a paid plan",
            AUDIT_COMMAND,
            repo_full_name,
        )
        return

    # A repo deselected from the installation (webhooks/installation.py's
    # hide_repo) - our access is already revoked; stay quiet rather than
    # verify a commenter's permission on a repo we can't act on anyway.
    if await is_repo_hidden(pool, installation_id, repo_full_name):
        return

    settings = get_settings()
    try:
        app_jwt = generate_app_jwt(settings.github_app_id, settings.github_app_private_key)
        permission = await asyncio.to_thread(
            _verify_commenter_permission_sync, installation_id, app_jwt, repo_full_name, commenter
        )
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
