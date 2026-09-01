import asyncio
import logging

from app_server.config import get_settings
from app_server.db import is_repo_hidden
from app_server.github_auth import generate_app_jwt, get_installation_token
from app_server.http_client import get_github_api_client
from scan_worker.github_api import fetch_default_branch_head_sha, fetch_pr_changed_files

logger = logging.getLogger(__name__)

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

PUSH_SCAN_JOB_TIMEOUT_SECONDS = 300


def _default_branch_head_and_pr_files_sync(
    installation_id: int, repo_full_name: str, base_sha: str, head_sha: str
) -> tuple[str, list[str]] | None:
    settings = get_settings()
    app_jwt = generate_app_jwt(settings.github_app_id, settings.github_app_private_key)
    token = get_installation_token(installation_id, app_jwt)
    client = get_github_api_client()
    default_branch_head = fetch_default_branch_head_sha(client, token, repo_full_name)
    if default_branch_head is None:
        return None
    changed_files = fetch_pr_changed_files(client, token, repo_full_name, base_sha, head_sha)
    return default_branch_head, changed_files


async def handle_pull_request_event(payload: dict, pool, redis_url: str, queue=None) -> None:
    action = payload.get("action")
    if action not in ENQUEUE_ACTIONS and action != "closed":
        return

    installation_id = payload["installation"]["id"]
    repo_full_name = payload["repository"]["full_name"]

    # A repo the customer deselected from the installation (see
    # webhooks/installation.py's hide_repo) - GitHub already revoked our
    # access, but the point of hiding is a clean no-op here rather than a
    # scan job that starts and then fails partway through.
    if await is_repo_hidden(pool, installation_id, repo_full_name):
        return

    if queue is None:
        from redis import Redis
        from rq import Queue

        queue = Queue("scans", connection=Redis.from_url(redis_url))

    if action == "closed":
        # A PR merging fires its own `push` event on the default branch
        # (handle_push_event / run_push_scan_job), which already reconciles
        # AIRview against the real merged code - nothing more to do here.
        #
        # But a PR simply CLOSED WITHOUT merging fires no push at all.
        # run_pr_scan_job (on "opened"/"synchronize" below) already updates
        # AIRview's live wiki straight off that PR's own proposed, possibly-
        # never-merged head - run_push_scan_job's own docstring names "a PR
        # closed without merging left the wiki describing abandoned branch
        # content forever" as the exact failure it exists to fix, but a
        # push-only trigger can't actually reach this case: nothing pushes
        # when a PR is simply closed. Real, confirmed gap (found by
        # re-verifying that docstring's claim against the actual webhook
        # wiring, not by trusting it) - without this, the wiki (and every
        # other get_latest_evidence reader: the dashboard, MCP tools) keeps
        # describing that abandoned PR's content indefinitely, until some
        # unrelated future push happens to correct it.
        #
        # Fixed the same way a merge already is: re-scan the CURRENT
        # default branch and run it through run_push_scan_job, scoped to
        # the closed PR's own changed files (the wiki entries this PR's own
        # scan could have contaminated) so this corrects exactly what needs
        # correcting, not a full rebuild.
        if not payload["pull_request"].get("merged"):
            try:
                result = await asyncio.to_thread(
                    _default_branch_head_and_pr_files_sync,
                    installation_id,
                    repo_full_name,
                    payload["pull_request"]["base"]["sha"],
                    payload["pull_request"]["head"]["sha"],
                )
            except Exception:
                logger.warning(
                    "failed to fetch default branch head / PR changed files for "
                    "closed-without-merge correction, installation=%s repo=%s pr=%s",
                    installation_id,
                    repo_full_name,
                    payload.get("number"),
                    exc_info=True,
                )
                return
            if result is not None:
                default_branch_head, changed_files = result
                if changed_files:
                    queue.enqueue(
                        "scan_worker.jobs.run_push_scan_job",
                        job_timeout=PUSH_SCAN_JOB_TIMEOUT_SECONDS,
                        installation_id=installation_id,
                        repo_full_name=repo_full_name,
                        head_sha=default_branch_head,
                        changed_files=changed_files,
                    )
        return

    queue.enqueue(
        "scan_worker.jobs.run_pr_scan_job",
        job_timeout=PR_SCAN_JOB_TIMEOUT_SECONDS,
        installation_id=installation_id,
        repo_full_name=repo_full_name,
        pr_number=payload["number"],
        base_sha=payload["pull_request"]["base"]["sha"],
        head_sha=payload["pull_request"]["head"]["sha"],
    )
    queue.enqueue(
        "scan_worker.jobs.run_flash_review_job",
        job_timeout=FLASH_REVIEW_JOB_TIMEOUT_SECONDS,
        installation_id=installation_id,
        repo_full_name=repo_full_name,
        pr_number=payload["number"],
        base_sha=payload["pull_request"]["base"]["sha"],
        head_sha=payload["pull_request"]["head"]["sha"],
    )
