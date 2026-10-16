import asyncio
import logging

from app_server.config import get_settings
from app_server.db import get_installation, is_repo_hidden
from app_server.github_auth import generate_app_jwt, get_installation_token, get_repo_permission_for_user
from app_server.rate_limit import is_rate_limited
from app_server.webhooks.comment_commands import command_candidate_lines, matches_command

logger = logging.getLogger(__name__)

AUDIT_COMMAND = "/aletheore audit"

# Real gap found via audit: this ChatOps trigger is reachable by ANY GitHub
# account that can comment on a PR of a public repo - unlike an API token,
# there is nothing to revoke. Verifying a commenter's permission costs 2
# real outbound GitHub API calls (mint an installation token, then a
# collaborator-permission lookup), charged against the whole installation's
# shared GitHub API quota, for every comment that merely matches the
# command text - before that commenter is known to have any access at all.
# Deliberately tight relative to real usage - a maintainer typing this
# command is a rare, deliberate action, not routine traffic.
#
# Keyed by (installation, commenter), not installation alone - Flash
# Review caught the real flaw in an installation-only key: the check runs
# BEFORE permission is verified, so any GitHub account (no access needed)
# could drain the whole installation's shared budget with 20 comments and
# silently lock out a legitimate maintainer's own attempt right after,
# turning this DoS guard into a cheap DoS against the installation's own
# users. Per-commenter keying means an attacker can only ever exhaust
# their own bucket; a real maintainer's is untouched by someone else's
# spam. Scaling the attack now needs many distinct GitHub accounts, not
# one - a much higher bar, and the one the original GitHub-API-quota
# concern was actually about.
COMMENT_COMMAND_RATE_LIMIT = 20
COMMENT_COMMAND_RATE_LIMIT_WINDOW_SECONDS = 3600

# Re-exported under their old private names: comment_commands.py now holds
# the real implementation (shared with pull_request_review_comment.py's
# /dismiss gate, see that module's own history for why), but this module
# keeps these names since tests and call sites below already use them.
_matches_command = matches_command
_command_candidate_lines = command_candidate_lines


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


async def _is_comment_command_rate_limited(installation_id: int, commenter: str) -> bool:
    from app_server.redis_client import get_redis_client

    try:
        # is_rate_limited uses the synchronous redis-py client and blocks on
        # pipe.execute() - run off the event loop, same reasoning as every
        # other rate limit in this service.
        return await asyncio.to_thread(
            is_rate_limited,
            get_redis_client(),
            f"ratelimit:comment-command:{installation_id}:{commenter}",
            COMMENT_COMMAND_RATE_LIMIT,
            COMMENT_COMMAND_RATE_LIMIT_WINDOW_SECONDS,
        )
    except Exception as exc:  # noqa: BLE001
        # Fails open, matching every other rate limit in this service - a
        # Redis outage should cost this throttle, not webhook availability.
        # Unlike the permission check below, this gate isn't a security
        # boundary on its own (GitHub's own per-account posting limits and
        # the permission check that still follows both still apply); it
        # only bounds amplification, so failing open here is a far smaller
        # concession than the permission check's own fail-closed stance.
        logger.warning("comment-command rate limit check failed (%s); allowing request", exc)
        return False


async def handle_issue_comment_event(payload: dict, pool, redis_url: str, queue=None) -> None:
    if payload.get("action") != "created":
        return
    if "pull_request" not in payload.get("issue", {}):
        return
    comment = payload.get("comment", {})
    body = comment.get("body", "")
    if not any(_matches_command(line, AUDIT_COMMAND) for line in _command_candidate_lines(body)):
        return
    if comment.get("user", {}).get("type") == "Bot":
        return

    installation_id = payload["installation"]["id"]
    repo_full_name = payload["repository"]["full_name"]
    commenter = payload["comment"]["user"]["login"]

    # Managed audits are AIR-exclusive (see managed_audit_api.py's own 402
    # for the HTTP trigger, "!= air" not "== free") - the flash tier does
    # not include managed audits at all, same as it doesn't include
    # AIRview/Docs/the managed dashboard. This ChatOps trigger used to
    # check "== free" (matching managed_audit_api.py's own pre-flash-tier
    # check), which meant a flash installation - a real, live gap found
    # during a full-session final audit - passed straight through: any
    # commenter with write access on a $6/mo flash repo could type
    # "/aletheore audit" and get a full, real managed-audit run, a
    # meaningfully more expensive LLM workload than the PR reviews the
    # tier is actually priced for. Silent on rejection, matching the
    # permission-denied case right below: this fires from any commenter
    # with write access, not just the installer, so it's not obviously a
    # billing question they're asking - same reasoning as staying quiet
    # on a permission check failure rather than narrating access details
    # to whoever happens to comment.
    installation = await get_installation(pool, installation_id)
    if installation is None or installation["plan"] != "air":
        logger.info(
            "ignoring '%s' on %s: managed audits require the AIR plan",
            AUDIT_COMMAND,
            repo_full_name,
        )
        return

    # A repo deselected from the installation (webhooks/installation.py's
    # hide_repo) - our access is already revoked; stay quiet rather than
    # verify a commenter's permission on a repo we can't act on anyway.
    if await is_repo_hidden(pool, installation_id, repo_full_name):
        return

    if await _is_comment_command_rate_limited(installation_id, commenter):
        logger.info(
            "ignoring '%s' on %s: %s is over the comment-command rate limit",
            AUDIT_COMMAND,
            repo_full_name,
            commenter,
        )
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
