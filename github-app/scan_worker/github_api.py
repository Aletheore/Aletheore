import base64

import httpx

from aletheore.pr_comment import COMMENT_MARKER

MAX_CONTEXT_FILES = 30
MAX_CONTEXT_FILE_BYTES = 80_000
MAX_CONTEXT_TOTAL_BYTES = 400_000


class PRDiff(str):
    """Flattened diff text plus structured patches from GitHub."""

    def __new__(cls, text: str, patches: tuple[tuple[str, str], ...]):
        value = str.__new__(cls, text)
        value.patches = patches
        return value


class BranchNotOwnedByAletheoreError(Exception):
    """Raised by ensure_branch_at when a branch with our reserved name
    already exists but its HEAD commit wasn't made by us - force-pushing
    over it would silently destroy someone else's work (e.g. a
    contributor who happened to push to a branch with the same name)."""


def upsert_pr_comment(
    client: httpx.Client,
    token: str,
    repo_full_name: str,
    pr_number: int,
    body: str,
    marker: str = COMMENT_MARKER,
) -> None:
    headers = {
        "Authorization": f"token {token}",
        "Accept": "application/vnd.github+json",
    }
    comments_url = f"/repos/{repo_full_name}/issues/{pr_number}/comments"
    response = client.get(comments_url, headers=headers)
    response.raise_for_status()
    existing = next(
        (comment for comment in response.json() if marker in comment.get("body", "")),
        None,
    )

    if existing:
        response = client.patch(
            f"/repos/{repo_full_name}/issues/comments/{existing['id']}",
            headers=headers,
            json={"body": body},
        )
    else:
        response = client.post(comments_url, headers=headers, json={"body": body})
    response.raise_for_status()


def create_check_run(
    client: httpx.Client,
    token: str,
    repo_full_name: str,
    head_sha: str,
    conclusion: str,
    summary: str,
    name: str = "Aletheore secrets check",
) -> None:
    headers = {
        "Authorization": f"token {token}",
        "Accept": "application/vnd.github+json",
    }
    response = client.post(
        f"/repos/{repo_full_name}/check-runs",
        headers=headers,
        json={
            "name": name,
            "head_sha": head_sha,
            "status": "completed",
            "conclusion": conclusion,
            "output": {"title": name, "summary": summary},
        },
    )
    response.raise_for_status()


def fetch_pr_diff(
    client: httpx.Client,
    token: str,
    repo_full_name: str,
    base_ref: str,
    head_ref: str,
) -> PRDiff:
    headers = {
        "Authorization": f"token {token}",
        "Accept": "application/vnd.github+json",
    }
    response = client.get(
        f"/repos/{repo_full_name}/compare/{base_ref}...{head_ref}",
        headers=headers,
    )
    response.raise_for_status()
    parts = []
    patches = []
    for file in response.json().get("files", []):
        patch = file.get("patch")
        if patch:
            patches.append((file["filename"], patch))
            parts.append(f"--- {file['filename']} ---\n{patch}")
    return PRDiff("\n\n".join(parts), tuple(patches))


def fetch_pr_changed_files(
    client: httpx.Client,
    token: str,
    repo_full_name: str,
    base_ref: str,
    head_ref: str,
) -> list[str]:
    headers = {
        "Authorization": f"token {token}",
        "Accept": "application/vnd.github+json",
    }
    response = client.get(
        f"/repos/{repo_full_name}/compare/{base_ref}...{head_ref}",
        headers=headers,
    )
    response.raise_for_status()
    return [file["filename"] for file in response.json().get("files", [])]


def fetch_pr_context(
    client: httpx.Client,
    token: str,
    repo_full_name: str,
    pr_number: int,
) -> str:
    """Return bounded human-authored PR context for review when available."""
    headers = {
        "Authorization": f"token {token}",
        "Accept": "application/vnd.github+json",
    }
    response = client.get(f"/repos/{repo_full_name}/pulls/{pr_number}", headers=headers)
    response.raise_for_status()
    payload = response.json()
    title = str(payload.get("title") or "").strip()
    body = str(payload.get("body") or "").strip()
    parts = ["--- pull request context (author-provided, untrusted) ---"]
    if title:
        parts.append(f"title: {title[:500]}")
    if body:
        parts.append(f"body:\n{body[:7_500]}")
    return "\n".join(parts) if len(parts) > 1 else ""


def fetch_default_branch_head_sha(
    client: httpx.Client,
    token: str,
    repo_full_name: str,
) -> str | None:
    headers = {
        "Authorization": f"token {token}",
        "Accept": "application/vnd.github+json",
    }
    repo_response = client.get(f"/repos/{repo_full_name}", headers=headers)
    repo_response.raise_for_status()
    default_branch = repo_response.json()["default_branch"]

    commit_response = client.get(
        f"/repos/{repo_full_name}/commits/{default_branch}",
        headers=headers,
    )
    # 409 is GitHub's actual response for "this repository has no commits
    # yet" on the commits endpoint - a normal state for a freshly created
    # or freshly connected repo, not an error. Distinct from a 404 (repo
    # or ref doesn't exist at all), which still raises below.
    if commit_response.status_code == 409:
        return None
    commit_response.raise_for_status()
    return commit_response.json()["sha"]


def fetch_default_branch_and_head_sha(
    client: httpx.Client,
    token: str,
    repo_full_name: str,
) -> tuple[str, str]:
    """Same two calls as fetch_default_branch_head_sha, but also returns the
    branch name - for a caller (sync_docs_to_repo) that needs both: the sha
    to reset a bot-owned branch onto, and the name as the PR's base. Calling
    fetch_default_branch_head_sha() plus a separate fetch_default_branch()
    would fetch GET /repos/{repo} twice for the exact same default_branch
    value."""
    headers = {
        "Authorization": f"token {token}",
        "Accept": "application/vnd.github+json",
    }
    repo_response = client.get(f"/repos/{repo_full_name}", headers=headers)
    repo_response.raise_for_status()
    default_branch = repo_response.json()["default_branch"]

    commit_response = client.get(
        f"/repos/{repo_full_name}/commits/{default_branch}",
        headers=headers,
    )
    commit_response.raise_for_status()
    return default_branch, commit_response.json()["sha"]


def fetch_file_content(
    client: httpx.Client,
    token: str,
    repo_full_name: str,
    path: str,
    ref: str | None = None,
) -> str | None:
    headers = {
        "Authorization": f"token {token}",
        "Accept": "application/vnd.github+json",
    }
    # ref=None omits the query param entirely rather than sending a literal
    # "HEAD" or similar - GitHub's Contents API only accepts a real
    # branch/tag/commit ref, and resolves to the repo's default branch
    # automatically when the param is absent.
    response = client.get(
        f"/repos/{repo_full_name}/contents/{path}",
        headers=headers,
        params={"ref": ref} if ref else {},
    )
    if response.status_code == 404:
        return None
    response.raise_for_status()
    data = response.json()
    if data.get("encoding") != "base64" or not data.get("content"):
        return None
    try:
        return base64.b64decode(data["content"]).decode("utf-8")
    except (ValueError, UnicodeDecodeError):
        return None


def ensure_branch_at(
    client: httpx.Client,
    token: str,
    repo_full_name: str,
    branch: str,
    target_sha: str,
    expected_committer_login: str,
) -> None:
    """Points `branch` at target_sha, creating it if it doesn't exist yet or
    force-resetting it if it does - used for a bot-owned branch that should
    always be exactly "latest default branch + our one file change", never
    accumulating drift from earlier runs.

    Before force-resetting an existing branch, verifies its HEAD commit was
    actually made by us (GitHub attributes commits made via an installation
    token to `{app_slug}[bot]`). Someone else could push a branch with this
    same reserved name (accidentally, or otherwise) - without this check
    we'd silently force-push over and destroy whatever was there."""
    headers = {
        "Authorization": f"token {token}",
        "Accept": "application/vnd.github+json",
    }
    existing = client.get(f"/repos/{repo_full_name}/git/ref/heads/{branch}", headers=headers)
    if existing.status_code == 404:
        response = client.post(
            f"/repos/{repo_full_name}/git/refs",
            headers=headers,
            json={"ref": f"refs/heads/{branch}", "sha": target_sha},
        )
        response.raise_for_status()
        return
    existing.raise_for_status()
    existing_sha = existing.json()["object"]["sha"]

    commit = client.get(f"/repos/{repo_full_name}/commits/{existing_sha}", headers=headers)
    commit.raise_for_status()
    committer = commit.json().get("committer") or {}
    if committer.get("login") != expected_committer_login:
        raise BranchNotOwnedByAletheoreError(
            f"refusing to force-push {repo_full_name}:{branch}: existing HEAD commit "
            f"{existing_sha} was committed by {committer.get('login')!r}, not "
            f"{expected_committer_login!r}"
        )

    response = client.patch(
        f"/repos/{repo_full_name}/git/refs/heads/{branch}",
        headers=headers,
        json={"sha": target_sha, "force": True},
    )
    response.raise_for_status()


def upsert_repo_file(
    client: httpx.Client,
    token: str,
    repo_full_name: str,
    path: str,
    branch: str,
    content: str,
    message: str,
) -> None:
    """Creates or updates a single file on `branch` via the Contents API -
    simpler than the Git Data (tree/commit) API and sufficient since this
    is always exactly one file."""
    headers = {
        "Authorization": f"token {token}",
        "Accept": "application/vnd.github+json",
    }
    existing_sha = None
    existing = client.get(
        f"/repos/{repo_full_name}/contents/{path}", headers=headers, params={"ref": branch}
    )
    if existing.status_code == 200:
        existing_sha = existing.json().get("sha")
    elif existing.status_code != 404:
        existing.raise_for_status()

    payload = {
        "message": message,
        "content": base64.b64encode(content.encode("utf-8")).decode("ascii"),
        "branch": branch,
    }
    if existing_sha is not None:
        payload["sha"] = existing_sha
    response = client.put(f"/repos/{repo_full_name}/contents/{path}", headers=headers, json=payload)
    response.raise_for_status()


def find_open_pull_request(
    client: httpx.Client,
    token: str,
    repo_full_name: str,
    head_branch: str,
) -> int | None:
    headers = {
        "Authorization": f"token {token}",
        "Accept": "application/vnd.github+json",
    }
    owner = repo_full_name.split("/", 1)[0]
    response = client.get(
        f"/repos/{repo_full_name}/pulls",
        headers=headers,
        params={"head": f"{owner}:{head_branch}", "state": "open"},
    )
    response.raise_for_status()
    pulls = response.json()
    return pulls[0]["number"] if pulls else None


def create_pull_request(
    client: httpx.Client,
    token: str,
    repo_full_name: str,
    head_branch: str,
    base_branch: str,
    title: str,
    body: str,
) -> int:
    headers = {
        "Authorization": f"token {token}",
        "Accept": "application/vnd.github+json",
    }
    response = client.post(
        f"/repos/{repo_full_name}/pulls",
        headers=headers,
        json={"title": title, "head": head_branch, "base": base_branch, "body": body},
    )
    response.raise_for_status()
    return response.json()["number"]


def ensure_docs_pull_request(
    client: httpx.Client,
    token: str,
    repo_full_name: str,
    head_branch: str,
    base_branch: str,
    title: str,
    body: str,
) -> int:
    """Reuses an already-open PR from head_branch if one exists (the
    rolling-PR model - this branch is bot-owned, so at most one open PR
    from it is ever expected) rather than opening a duplicate every run."""
    existing_number = find_open_pull_request(client, token, repo_full_name, head_branch)
    if existing_number is not None:
        return existing_number
    return create_pull_request(client, token, repo_full_name, head_branch, base_branch, title, body)


def fetch_recent_commits_for_path(
    client: httpx.Client,
    token: str,
    repo_full_name: str,
    path: str,
    limit: int = 1,
) -> list[dict]:
    headers = {
        "Authorization": f"token {token}",
        "Accept": "application/vnd.github+json",
    }
    response = client.get(
        f"/repos/{repo_full_name}/commits",
        headers=headers,
        params={"path": path, "per_page": limit},
    )
    if response.status_code == 404:
        return []
    response.raise_for_status()
    commits = []
    for item in response.json():
        commit = item.get("commit", {})
        author = commit.get("author", {}) or {}
        message = commit.get("message") or ""
        commits.append(
            {
                "sha": item.get("sha"),
                "author": author.get("name"),
                "date": author.get("date"),
                "subject": message.split("\n", 1)[0],
            }
        )
    return commits
