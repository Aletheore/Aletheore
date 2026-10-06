import asyncio
import logging

from app_server.config import get_settings
from app_server.db import get_installation, is_repo_hidden
from app_server.github_auth import generate_app_jwt, get_installation_token, get_repo_permission_for_user

logger = logging.getLogger(__name__)

AUDIT_COMMAND = "/aletheore audit"


def _matches_command(line: str, command: str) -> bool:
    """True if `line` (already stripped) IS `command`, or starts with
    `command` followed by whitespace - not a bare string-prefix check.

    Real bug this closes: `line.startswith(command)` also matches an
    ordinary English word sharing the same stem - "/aletheore auditing
    this PR now" or "/aletheore auditorium" both satisfied the old
    check, on a GitHub PR thread whose entire subject is reviewing/
    auditing code, exactly the conversational context where a commenter
    typing a sentence starting with "audit..." is a real, not
    hypothetical, risk. Confirmed directly: both fired the real, billed,
    AIR-tier-gated managed-audit job with no intent to trigger it.
    """
    if line == command:
        return True
    return line.startswith(command) and line[len(command) : len(command) + 1].isspace()


def _fence_marker(stripped: str) -> tuple[str, int] | None:
    """Return (fence_char, run_length) if `stripped` is a fence marker line
    (a run of 3+ backticks or tildes), else None."""
    if not stripped:
        return None
    char = stripped[0]
    if char not in ("`", "~"):
        return None
    length = len(stripped) - len(stripped.lstrip(char))
    if length < 3:
        return None
    return char, length


def _command_candidate_lines(body: str):
    """Lines of a comment that could be a real command invocation: not inside
    a fenced code block and not an indented (4 spaces / tab) code block, so a
    maintainer documenting the command doesn't fire a real billed audit.

    Real bug this closes: comparing only the first 3 characters of a line
    to decide whether it closes a fence let a closing marker SHORTER than
    the opening one (e.g. a literal ``` line documented inside a ````-fenced
    block) end tracking early, exposing a command still inside the real
    fence per GitHub's own CommonMark/GFM rendering (a closing fence must
    be >= the opening fence's length). Confirmed directly: this previously
    let a billed audit fire from a comment whose command was, visually and
    per GitHub's rendering, inside a code block.
    """
    fence_char: str | None = None
    fence_len = 0
    for raw in body.splitlines():
        stripped = raw.strip()
        marker = _fence_marker(stripped)
        if marker is not None:
            char, length = marker
            if fence_char is None:
                fence_char, fence_len = char, length
                continue
            if char == fence_char and length >= fence_len and stripped[length:].strip() == "":
                fence_char = None
                fence_len = 0
                continue
            # A fence-shaped line that doesn't close the open fence (too
            # short, wrong character, or has trailing content) is literal
            # content inside the fence - fall through to the check below.
        if fence_char is not None:
            continue
        if raw.startswith(("    ", "\t")):
            continue
        yield stripped


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
