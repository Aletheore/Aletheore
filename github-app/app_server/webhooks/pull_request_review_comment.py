import asyncio
import logging

from app_server.config import get_settings
from app_server.db import get_flash_review_finding_comment_by_github_id, is_repo_hidden
from app_server.dismissed_findings import dismiss_finding_by_identity_key
from app_server.github_auth import generate_app_jwt, get_installation_token, get_repo_permission_for_user

logger = logging.getLogger(__name__)

DISMISS_COMMAND = "/dismiss"

# Same reasoning as issue_comment.py's AUDIT_COMMAND gate: anyone who can
# push to the repo can already suppress a finding by editing the code
# around it or disabling the check entirely - write/admin is the same bar
# an outside PR commenter (who this exists to stop) sits below.
AUTHORIZED_PERMISSIONS = ("admin", "write")


def _verify_commenter_permission_sync(
    installation_id: int, app_jwt: str, repo_full_name: str, commenter: str
) -> str:
    token = get_installation_token(installation_id, app_jwt)
    return get_repo_permission_for_user(repo_full_name, commenter, token)


def _dismiss_reason(body: str) -> str | None:
    """Whatever follows /dismiss on its own line, trimmed - None if the
    command is bare (no reason given). Only the first matching line is
    used; a reply is one short comment, not a document."""
    for line in body.splitlines():
        stripped = line.strip()
        if stripped.startswith(DISMISS_COMMAND):
            reason = stripped[len(DISMISS_COMMAND):].strip()
            return reason or None
    return None


async def handle_pull_request_review_comment_event(payload: dict, pool, redis_url: str, queue=None) -> None:
    """Reply-based dismissal: a user replies /dismiss to one of Flash
    Review's inline finding comments (see jobs.py's
    _flash_review_comment_body, which tells them to). GitHub fires this
    event for every reply to a review comment - in_reply_to_id is only
    ever set on a reply, never a top-level review comment, which is what
    scopes this to actual replies without needing to distinguish by
    content first.

    Deliberately does not verify this is a reply to specifically a Flash
    Review comment before doing the permission check - a reply to some
    unrelated review comment simply won't resolve via
    get_flash_review_finding_comment_by_github_id below and this returns
    quietly, same cost either way. Checking permission first would only
    save one DB query on the common case of "not our comment", not worth
    reordering for.
    """
    if payload.get("action") != "created":
        return
    comment = payload.get("comment", {})
    if comment.get("in_reply_to_id") is None:
        return  # not a reply - a top-level review comment, nothing to dismiss
    if comment.get("user", {}).get("type") == "Bot":
        return  # never act on our own resolution-edit comments or any other bot's reply
    body = comment.get("body", "")
    if not any(line.strip().startswith(DISMISS_COMMAND) for line in body.splitlines()):
        return

    installation_id = payload["installation"]["id"]
    repo_full_name = payload["repository"]["full_name"]
    commenter = payload["comment"]["user"]["login"]
    in_reply_to_id = comment["in_reply_to_id"]

    if await is_repo_hidden(pool, installation_id, repo_full_name):
        return

    tracked = await get_flash_review_finding_comment_by_github_id(pool, in_reply_to_id)
    if tracked is None:
        # A reply to a review comment we don't track (not one of Flash
        # Review's own finding comments, or the tracking row was never
        # written) - nothing to dismiss.
        return
    if tracked["installation_id"] != installation_id or tracked["repo_full_name"] != repo_full_name:
        # The tracked row belongs to a different installation/repo than
        # this webhook payload claims - in_reply_to_id is a GitHub-wide
        # comment id, not scoped to one installation, so this is the
        # cross-check that stops a payload from one installation acting on
        # another's dismissal state. Should never happen in practice (a
        # reply's own repository IS the comment's repository), but the
        # check is cheap and the alternative (trusting the id blindly) is
        # not.
        logger.warning(
            "flash review dismiss: in_reply_to_id=%s tracked row belongs to a different "
            "installation/repo than the webhook payload (%s/%s) - refusing to act",
            in_reply_to_id, installation_id, repo_full_name,
        )
        return

    settings = get_settings()
    try:
        app_jwt = generate_app_jwt(settings.github_app_id, settings.github_app_private_key)
        permission = await asyncio.to_thread(
            _verify_commenter_permission_sync, installation_id, app_jwt, repo_full_name, commenter
        )
    except Exception:
        # Fail closed, same as issue_comment.py's audit trigger: an API
        # hiccup here should silently drop a legitimate dismissal (the
        # user can just reply again) rather than let an unverified
        # commenter through because the check itself errored.
        logger.warning(
            "failed to verify commenter permission for %s on %s; refusing to record dismissal",
            commenter, repo_full_name, exc_info=True,
        )
        return

    if permission not in AUTHORIZED_PERMISSIONS:
        logger.info(
            "ignoring '%s' from %s on %s: permission=%r, need write or admin",
            DISMISS_COMMAND, commenter, repo_full_name, permission,
        )
        return

    await dismiss_finding_by_identity_key(
        pool,
        installation_id,
        repo_full_name,
        tracked["finding_type"],
        tracked["identity_key"],
        dismissed_by=commenter,
        reason=_dismiss_reason(body),
    )
