import asyncio
import logging

from app_server.config import get_settings
from app_server.github_auth import generate_app_jwt, get_installation_token
from app_server.http_client import get_github_api_client

logger = logging.getLogger(__name__)

COMPARE_FILES_PER_PAGE = 100


def _changed_files_from_commits(commits: list[dict]) -> set[str]:
    changed_files: set[str] = set()
    for commit in commits:
        changed_files.update(commit.get("added", []))
        changed_files.update(commit.get("removed", []))
        changed_files.update(commit.get("modified", []))
    return changed_files


def _push_payload_commits_truncated(payload: dict) -> bool:
    size = payload.get("size")
    commits = payload.get("commits", [])
    return isinstance(size, int) and size > len(commits)


def _fetch_compare_changed_files_sync(
    installation_id: int, repo_full_name: str, before_sha: str, after_sha: str
) -> set[str]:
    settings = get_settings()
    app_jwt = generate_app_jwt(settings.github_app_id, settings.github_app_private_key)
    token = get_installation_token(installation_id, app_jwt)
    client = get_github_api_client()
    headers = {
        "Authorization": f"Bearer {token}",
        "Accept": "application/vnd.github+json",
    }
    changed_files: set[str] = set()
    page = 1

    while True:
        response = client.get(
            f"/repos/{repo_full_name}/compare/{before_sha}...{after_sha}",
            headers=headers,
            params={"per_page": COMPARE_FILES_PER_PAGE, "page": page},
        )
        response.raise_for_status()
        files = response.json().get("files", [])
        for file_info in files:
            filename = file_info.get("filename")
            if filename:
                changed_files.add(filename)
            previous_filename = file_info.get("previous_filename")
            if previous_filename:
                changed_files.add(previous_filename)
        if len(files) < COMPARE_FILES_PER_PAGE:
            break
        page += 1

    return changed_files


async def _changed_files_for_push(payload: dict) -> set[str]:
    commits = payload.get("commits", [])
    if not _push_payload_commits_truncated(payload):
        return _changed_files_from_commits(commits)

    installation_id = payload["installation"]["id"]
    repo_full_name = payload["repository"]["full_name"]
    before_sha = payload.get("before")
    after_sha = payload["after"]
    logger.warning(
        "push webhook commit list truncated for installation=%s repo=%s "
        "(payload_size=%s commits_in_payload=%s); fetching changed files via compare API",
        installation_id,
        repo_full_name,
        payload.get("size"),
        len(commits),
    )
    try:
        return await asyncio.to_thread(
            _fetch_compare_changed_files_sync,
            installation_id,
            repo_full_name,
            before_sha,
            after_sha,
        )
    except Exception:
        logger.warning(
            "failed to fetch compare files for truncated push installation=%s repo=%s before=%s after=%s",
            installation_id,
            repo_full_name,
            before_sha,
            after_sha,
            exc_info=True,
        )
        raise


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

    changed_files = await _changed_files_for_push(payload)

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
