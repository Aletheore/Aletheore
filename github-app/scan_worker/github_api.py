import base64

import httpx

from aletheore.pr_comment import COMMENT_MARKER

MAX_CONTEXT_FILES = 15
MAX_CONTEXT_FILE_BYTES = 40_000
MAX_CONTEXT_TOTAL_BYTES = 200_000


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
) -> None:
    headers = {
        "Authorization": f"token {token}",
        "Accept": "application/vnd.github+json",
    }
    response = client.post(
        f"/repos/{repo_full_name}/check-runs",
        headers=headers,
        json={
            "name": "Aletheore secrets check",
            "head_sha": head_sha,
            "status": "completed",
            "conclusion": conclusion,
            "output": {"title": "Aletheore secrets check", "summary": summary},
        },
    )
    response.raise_for_status()


def fetch_pr_diff(
    client: httpx.Client,
    token: str,
    repo_full_name: str,
    base_ref: str,
    head_ref: str,
) -> str:
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
    for file in response.json().get("files", []):
        patch = file.get("patch")
        if patch:
            parts.append(f"--- {file['filename']} ---\n{patch}")
    return "\n\n".join(parts)


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


def fetch_file_content(
    client: httpx.Client,
    token: str,
    repo_full_name: str,
    path: str,
    ref: str,
) -> str | None:
    headers = {
        "Authorization": f"token {token}",
        "Accept": "application/vnd.github+json",
    }
    response = client.get(
        f"/repos/{repo_full_name}/contents/{path}",
        headers=headers,
        params={"ref": ref},
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
