import base64
import difflib
import re

import httpx

from aletheore.pr_comment import COMMENT_MARKER

MAX_CONTEXT_FILES = 30
MAX_CONTEXT_FILE_BYTES = 80_000
MAX_CONTEXT_TOTAL_BYTES = 400_000

# Real, measured (not assumed) on Flash Review's own benchmark corpus (25
# cases, real gpt-5.6-luna calls): trimming GitHub's default 3-line hunk
# context down to 1 held recall at parity with the untrimmed diff (noise-
# level churn either way, no real bugs lost) while false positives on
# clean cases actually dropped (1/4 -> 0/4) and cost fell ~5.7%. Zero
# context (0 lines) was tested too and rejected - that one cost 4 real
# bugs (20/21 -> 16/21) for a similar saving, a real recall loss, not
# just noise. 1 is the validated number; do not change it without a real
# rerun of that same corpus.
DIFF_PROMPT_CONTEXT_LINES = 1

_GITHUB_HUNK_HEADER_RE = re.compile(r"^@@ -(\d+)(?:,(\d+))? \+(\d+)(?:,(\d+))? @@(.*)$")
_DIFFLIB_HUNK_HEADER_RE = re.compile(r"^@@ -(\d+)(?:,(\d+))? \+(\d+)(?:,(\d+))? @@$")


def _iter_patch_hunks(patch: str):
    """Yield (header_match, body_lines) for each @@ hunk in a GitHub patch."""
    header = None
    body: list[str] = []
    for line in patch.splitlines():
        match = _GITHUB_HUNK_HEADER_RE.match(line)
        if match:
            if header is not None:
                yield header, body
            header, body = match, []
        elif header is not None:
            body.append(line)
    if header is not None:
        yield header, body


def _trim_patch_context(patch: str, context_lines: int = DIFF_PROMPT_CONTEXT_LINES) -> str:
    """Re-derive this patch with fewer unchanged context lines around each
    change than GitHub's own default (3) - every actual +/- line survives
    unchanged, only the surrounding context shrinks.

    Only ever used to build the copy of the diff that goes into the
    model's prompt (see fetch_pr_diff below) - grounding/citation
    validation (_validate_findings) always uses GitHub's own untrimmed
    patches via PRDiff.patches, never this trimmed text, so a bug here
    can only make the prompt wrong, never silently weaken what a finding
    gets validated against.

    Reconstructs each hunk's real old/new text from the hunk's own body -
    a context line already appears in both versions, a removed line is
    old-only, an added line is new-only, so nothing needs fetching beyond
    what GitHub's patch already contains - then re-diffs with
    difflib.unified_diff, which handles correct hunk-splitting and header
    math itself. Hand-rolling that arithmetic was considered and rejected:
    a line-number bug in it would be silent and hard to catch, whereas
    difflib is a well-tested standard-library diff implementation doing
    exactly the computation this needs.
    """
    out: list[str] = []
    for header, body in _iter_patch_hunks(patch):
        old_start = int(header.group(1))
        new_start = int(header.group(3))
        old_lines: list[str] = []
        new_lines: list[str] = []
        for line in body:
            tag = line[:1]
            text = line[1:]
            if tag == "-":
                old_lines.append(text)
            elif tag == "+":
                new_lines.append(text)
            else:
                old_lines.append(text)
                new_lines.append(text)

        diff_lines = list(
            difflib.unified_diff(old_lines, new_lines, n=context_lines, lineterm="")
        )
        for line in diff_lines:
            if line.startswith("---") or line.startswith("+++"):
                continue
            match = _DIFFLIB_HUNK_HEADER_RE.match(line)
            if match is None:
                out.append(line)
                continue
            rel_old_start = int(match.group(1))
            rel_new_start = int(match.group(3))
            old_count = int(match.group(2) or 1)
            new_count = int(match.group(4) or 1)
            # difflib reports a zero-count range's position as 0, not 1 -
            # its own "insertion point" convention, already directly
            # anchored with no further off-by-one adjustment needed.
            # Every non-empty range is 1-based, so -1 converts it to a
            # real offset from old_start/new_start - applying that same
            # -1 to an empty range would shift a real "-5,0" (insert
            # after old line 5) into an incorrect "-4,0".
            real_old_start = old_start + rel_old_start - (1 if old_count else 0)
            real_new_start = new_start + rel_new_start - (1 if new_count else 0)
            trailing = header.group(5) or ""
            out.append(f"@@ -{real_old_start},{old_count} +{real_new_start},{new_count} @@{trailing}")
    return "\n".join(out)


class PRDiff(str):
    """Flattened diff text plus structured patches from GitHub."""

    def __new__(
        cls,
        text: str,
        patches: tuple[tuple[str, str], ...],
        omitted_files: tuple[str, ...] = (),
    ):
        value = str.__new__(cls, text)
        value.patches = patches
        # Changed files GitHub's own compare API gave no patch for, and whose
        # content couldn't be reconstructed either (binary, too large, or a
        # fetch failure) - see fetch_pr_diff. These are genuinely invisible
        # to review, not merely trimmed.
        value.omitted_files = omitted_files
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


def create_pr_review_comment(
    client: httpx.Client,
    token: str,
    repo_full_name: str,
    pr_number: int,
    commit_id: str,
    path: str,
    line: int,
    body: str,
) -> dict:
    """Posts one inline PR review comment anchored to a real file:line -
    the .../pulls/{pr}/comments endpoint, distinct from upsert_pr_comment's
    .../issues/{pr}/comments (a plain, unanchored PR-level comment). side
    is always RIGHT: line is always a new-file line number (see
    flash_review.py's _diff_valid_lines), matching the new/head version of
    the diff GitHub anchors RIGHT-side comments against.

    Returns the created comment's JSON (id is what callers persist in
    flash_review_finding_comments to track it across re-reviews).

    A path/line GitHub's own review-comment validation rejects (not part
    of the diff's added/context lines - can happen if the same finding's
    citation drifted between grounding and posting, though grounding
    should already prevent this) surfaces as a real 422 from raise_for_status
    - deliberately not swallowed here, since a caller silently losing a
    finding it meant to post is worse than a visible failure.
    """
    headers = {
        "Authorization": f"token {token}",
        "Accept": "application/vnd.github+json",
    }
    response = client.post(
        f"/repos/{repo_full_name}/pulls/{pr_number}/comments",
        headers=headers,
        json={
            "body": body,
            "commit_id": commit_id,
            "path": path,
            "line": line,
            "side": "RIGHT",
        },
    )
    response.raise_for_status()
    return response.json()


def edit_pr_review_comment(
    client: httpx.Client,
    token: str,
    repo_full_name: str,
    comment_id: int,
    body: str,
) -> None:
    """Edits an existing inline review comment in place - used both to
    update a still-present finding's body across re-reviews (if its issue
    text changed) and to mark one no longer detected without deleting it
    (see run_flash_review_job's resolution handling: a reply thread a human
    already engaged with must stay intact, so this edits rather than
    deletes)."""
    headers = {
        "Authorization": f"token {token}",
        "Accept": "application/vnd.github+json",
    }
    response = client.patch(
        f"/repos/{repo_full_name}/pulls/comments/{comment_id}",
        headers=headers,
        json={"body": body},
    )
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


# A file this large wasn't going to fit the review budget even if it could
# be diffed - not worth two extra fetches (base + head content) to find
# that out. Real gap this guards against staying invisible, not a
# performance concern: without this fallback, GitHub omitting `patch` for
# a large changed file (a vendored/minified bundle is exactly this shape -
# confirmed live against benchmarks/pr-review-benchmark's own case 007,
# where GitHub's compare API returned has_patch=false for lodash.js)
# silently dropped that file from the diff the model ever sees, with no
# signal anywhere that anything was lost.
MAX_RECONSTRUCTED_DIFF_FILE_BYTES = 2_000_000


def _reconstruct_missing_patch(
    client: httpx.Client,
    token: str,
    repo_full_name: str,
    path: str,
    base_ref: str,
    head_ref: str,
) -> str | None:
    """Best-effort local diff for a file GitHub's compare API gave no patch
    for. Returns None (never raises) for anything that isn't a clean win -
    a deleted/unreadable/binary/too-large file, or a file whose base and
    head content are byte-identical (GitHub's own omission wasn't hiding a
    real change) - so the caller can fall back to just recording the file
    as genuinely omitted rather than surfacing a wrong or noisy diff.
    """
    head_content = fetch_file_content(client, token, repo_full_name, path, head_ref)
    if head_content is None or len(head_content.encode("utf-8")) > MAX_RECONSTRUCTED_DIFF_FILE_BYTES:
        return None
    base_content = fetch_file_content(client, token, repo_full_name, path, base_ref)
    if base_content is not None and len(base_content.encode("utf-8")) > MAX_RECONSTRUCTED_DIFF_FILE_BYTES:
        return None
    if base_content == head_content:
        return None
    base_lines = (base_content or "").splitlines(keepends=True)
    head_lines = head_content.splitlines(keepends=True)
    diff_lines = [
        line
        for line in difflib.unified_diff(base_lines, head_lines, lineterm="")
        if not (line.startswith("--- ") or line.startswith("+++ "))
    ]
    if not diff_lines:
        return None
    return "\n".join(line.rstrip("\n") for line in diff_lines)


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
    omitted_files = []
    for file in response.json().get("files", []):
        patch = file.get("patch")
        if not patch:
            # GitHub omits `patch` both for binary files and for text files
            # it considers too large/complex to diff - reconstructing from
            # the two full file versions recovers the second case (and
            # naturally still fails, cleanly, for the first: a binary
            # file's content fails fetch_file_content's utf-8 decode and
            # comes back None).
            patch = _reconstruct_missing_patch(
                client, token, repo_full_name, file["filename"], base_ref, head_ref
            )
        if patch:
            # Grounding always validates against the real, untrimmed patch
            # (patches, below) - only the text handed to the model shrinks.
            patches.append((file["filename"], patch))
            parts.append(f"--- {file['filename']} ---\n{_trim_patch_context(patch)}")
        else:
            omitted_files.append(file["filename"])
    return PRDiff("\n\n".join(parts), tuple(patches), tuple(omitted_files))


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
