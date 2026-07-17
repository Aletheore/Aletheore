import httpx

from aletheore.pr_comment import COMMENT_MARKER


def upsert_pr_comment(
    client: httpx.Client,
    token: str,
    repo_full_name: str,
    pr_number: int,
    body: str,
) -> None:
    headers = {
        "Authorization": f"token {token}",
        "Accept": "application/vnd.github+json",
    }
    comments_url = f"/repos/{repo_full_name}/issues/{pr_number}/comments"
    response = client.get(comments_url, headers=headers)
    response.raise_for_status()
    existing = next(
        (comment for comment in response.json() if COMMENT_MARKER in comment.get("body", "")),
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
