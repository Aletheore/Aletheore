import base64

import httpx

import pytest

from aletheore.pr_comment import COMMENT_MARKER
from scan_worker.github_api import (
    BranchNotOwnedByAletheoreError,
    create_check_run,
    create_pull_request,
    ensure_branch_at,
    ensure_docs_pull_request,
    fetch_default_branch_and_head_sha,
    fetch_default_branch_head_sha,
    fetch_file_content,
    fetch_pr_changed_files,
    fetch_pr_diff,
    fetch_recent_commits_for_path,
    find_open_pull_request,
    upsert_pr_comment,
    upsert_repo_file,
)


def test_creates_comment_when_none_exists():
    calls = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append((request.method, str(request.url)))
        if request.method == "GET":
            return httpx.Response(200, json=[])
        return httpx.Response(201, json={"id": 1})

    client = httpx.Client(transport=httpx.MockTransport(handler), base_url="https://api.github.com")
    upsert_pr_comment(client, "token", "octocat/hello-world", 42, f"{COMMENT_MARKER}\nbody")
    assert [method for method, _ in calls] == ["GET", "POST"]


def test_updates_existing_comment():
    calls = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append((request.method, str(request.url)))
        if request.method == "GET":
            return httpx.Response(200, json=[{"id": 99, "body": f"{COMMENT_MARKER}\nold body"}])
        return httpx.Response(200, json={"id": 99})

    client = httpx.Client(transport=httpx.MockTransport(handler), base_url="https://api.github.com")
    upsert_pr_comment(client, "token", "octocat/hello-world", 42, f"{COMMENT_MARKER}\nnew body")
    assert [method for method, _ in calls] == ["GET", "PATCH"]


def test_upsert_pr_comment_uses_custom_marker_when_given():
    calls = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append((request.method, request.content))
        if request.method == "GET":
            return httpx.Response(200, json=[{"id": 1, "body": f"{COMMENT_MARKER}\nold diff"}])
        return httpx.Response(201, json={"id": 2})

    client = httpx.Client(transport=httpx.MockTransport(handler), base_url="https://api.github.com")
    upsert_pr_comment(
        client,
        "token",
        "octocat/hello-world",
        42,
        "<!-- aletheore-audit -->\nnew audit",
        marker="<!-- aletheore-audit -->",
    )
    assert [method for method, _ in calls] == ["GET", "POST"]


def test_create_check_run_posts_expected_payload():
    calls = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        return httpx.Response(201, json={"id": 1})

    client = httpx.Client(transport=httpx.MockTransport(handler), base_url="https://api.github.com")
    create_check_run(client, "token", "octocat/hello-world", "abc123", "failure", "New secret found")

    assert len(calls) == 1
    request = calls[0]
    assert request.method == "POST"
    assert request.url.path == "/repos/octocat/hello-world/check-runs"
    import json as _json

    body = _json.loads(request.content)
    assert body["head_sha"] == "abc123"
    assert body["status"] == "completed"
    assert body["conclusion"] == "failure"
    assert body["name"] == "Aletheore secrets check"


def test_create_check_run_uses_custom_name_when_given():
    calls = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request.content)
        return httpx.Response(201, json={"id": 1})

    client = httpx.Client(
        transport=httpx.MockTransport(handler),
        base_url="https://api.github.com",
    )
    create_check_run(
        client,
        "token",
        "octocat/hello-world",
        "abc123",
        "neutral",
        "summary text",
        name="Aletheore regression risk",
    )

    import json as _json

    payload = _json.loads(calls[0])
    assert payload["name"] == "Aletheore regression risk"
    assert payload["conclusion"] == "neutral"


def test_fetch_pr_diff_concatenates_real_patches():
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/repos/octocat/hello-world/compare/aaa...bbb"
        return httpx.Response(
            200,
            json={
                "files": [
                    {
                        "filename": "app.py",
                        "patch": "@@ -1,2 +1,3 @@\n def hello():\n+    print('hi')\n     pass",
                    },
                    {"filename": "image.png", "patch": None},
                ]
            },
        )

    client = httpx.Client(transport=httpx.MockTransport(handler), base_url="https://api.github.com")
    diff_text = fetch_pr_diff(client, "fake-token", "octocat/hello-world", "aaa", "bbb")

    assert "app.py" in diff_text
    assert "print('hi')" in diff_text
    assert "image.png" not in diff_text
    assert diff_text.patches == (("app.py", "@@ -1,2 +1,3 @@\n def hello():\n+    print('hi')\n     pass"),)


def test_fetch_pr_diff_returns_empty_string_when_no_files_changed():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"files": []})

    client = httpx.Client(transport=httpx.MockTransport(handler), base_url="https://api.github.com")
    diff_text = fetch_pr_diff(client, "fake-token", "octocat/hello-world", "aaa", "bbb")

    assert diff_text == ""


def test_fetch_pr_changed_files_returns_filenames():
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/repos/octocat/hello-world/compare/aaa...bbb"
        return httpx.Response(
            200,
            json={"files": [{"filename": "app.py", "patch": "..."}, {"filename": "lib.py", "patch": "..."}]},
        )

    client = httpx.Client(transport=httpx.MockTransport(handler), base_url="https://api.github.com")
    result = fetch_pr_changed_files(client, "tok", "octocat/hello-world", "aaa", "bbb")

    assert result == ["app.py", "lib.py"]


def test_fetch_file_content_decodes_base64():
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/repos/octocat/hello-world/contents/app.py"
        assert request.url.params["ref"] == "bbb"
        content = base64.b64encode(b"print('hello')\n").decode()
        return httpx.Response(200, json={"content": content, "encoding": "base64", "size": 16})

    client = httpx.Client(transport=httpx.MockTransport(handler), base_url="https://api.github.com")
    result = fetch_file_content(client, "tok", "octocat/hello-world", "app.py", "bbb")

    assert result == "print('hello')\n"


def test_fetch_file_content_returns_none_for_404():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(404, json={"message": "Not Found"})

    client = httpx.Client(transport=httpx.MockTransport(handler), base_url="https://api.github.com")
    result = fetch_file_content(client, "tok", "octocat/hello-world", "deleted.py", "bbb")

    assert result is None


def test_fetch_file_content_returns_none_for_binary():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"content": "", "encoding": "none", "size": 12345})

    client = httpx.Client(transport=httpx.MockTransport(handler), base_url="https://api.github.com")
    result = fetch_file_content(client, "tok", "octocat/hello-world", "image.png", "bbb")

    assert result is None


def test_fetch_recent_commits_for_path_returns_shaped_commits():
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/repos/octocat/hello-world/commits"
        assert dict(request.url.params) == {
            "path": "controllers/user.controller.ts",
            "per_page": "1",
        }
        return httpx.Response(
            200,
            json=[
                {
                    "sha": "abc123def456",
                    "commit": {
                        "author": {
                            "name": "Ada Lovelace",
                            "date": "2026-07-23T10:00:00Z",
                        },
                        "message": "fix: guard against null user id\n\nlonger body here",
                    },
                }
            ],
        )

    client = httpx.Client(
        transport=httpx.MockTransport(handler),
        base_url="https://api.github.com",
    )
    commits = fetch_recent_commits_for_path(
        client,
        "token",
        "octocat/hello-world",
        "controllers/user.controller.ts",
    )

    assert commits == [
        {
            "sha": "abc123def456",
            "author": "Ada Lovelace",
            "date": "2026-07-23T10:00:00Z",
            "subject": "fix: guard against null user id",
        }
    ]


def test_fetch_recent_commits_for_path_respects_limit():
    def handler(request: httpx.Request) -> httpx.Response:
        assert dict(request.url.params)["per_page"] == "3"
        return httpx.Response(200, json=[])

    client = httpx.Client(
        transport=httpx.MockTransport(handler),
        base_url="https://api.github.com",
    )
    fetch_recent_commits_for_path(client, "token", "octocat/hello-world", "app.py", limit=3)


def test_fetch_recent_commits_for_path_returns_empty_list_for_404():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(404, json={"message": "Not Found"})

    client = httpx.Client(
        transport=httpx.MockTransport(handler),
        base_url="https://api.github.com",
    )
    commits = fetch_recent_commits_for_path(
        client,
        "token",
        "octocat/hello-world",
        "deleted_file.py",
    )

    assert commits == []


def test_fetch_recent_commits_for_path_returns_empty_list_when_no_commits():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=[])

    client = httpx.Client(
        transport=httpx.MockTransport(handler),
        base_url="https://api.github.com",
    )
    commits = fetch_recent_commits_for_path(client, "token", "octocat/hello-world", "app.py")

    assert commits == []


def test_fetch_default_branch_and_head_sha_returns_name_and_sha():
    calls = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(str(request.url))
        if request.url.path.endswith("/repos/octocat/hello-world"):
            return httpx.Response(200, json={"default_branch": "trunk"})
        return httpx.Response(200, json={"sha": "abc123"})

    client = httpx.Client(transport=httpx.MockTransport(handler), base_url="https://api.github.com")
    assert fetch_default_branch_and_head_sha(client, "token", "octocat/hello-world") == ("trunk", "abc123")
    # One /repos/{repo} call, not two - this replaced two separately-called
    # functions that each fetched it for the same default_branch value.
    assert len(calls) == 2
    assert calls[0].endswith("/repos/octocat/hello-world")
    assert calls[1].endswith("/repos/octocat/hello-world/commits/trunk")


def test_fetch_default_branch_head_sha_returns_none_for_empty_repo_409():
    # Found live: a real installation's fresh, genuinely-empty repo (no
    # commits pushed yet) sent run_initial_scan_job an unhandled
    # HTTPStatusError, firing an ops alert for what's actually a normal,
    # expected state - GitHub's commits endpoint 409s specifically for a
    # repo with no commits at all, distinct from a 404 (missing repo/ref).
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/repos/octocat/hello-world"):
            return httpx.Response(200, json={"default_branch": "main"})
        return httpx.Response(409, json={"message": "Git Repository is empty."})

    client = httpx.Client(transport=httpx.MockTransport(handler), base_url="https://api.github.com")
    assert fetch_default_branch_head_sha(client, "token", "octocat/hello-world") is None


def test_ensure_branch_at_creates_ref_when_missing():
    calls = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append((request.method, str(request.url)))
        if request.method == "GET":
            return httpx.Response(404)
        return httpx.Response(201, json={"ref": "refs/heads/aletheore/docs-update"})

    client = httpx.Client(transport=httpx.MockTransport(handler), base_url="https://api.github.com")
    ensure_branch_at(client, "token", "octocat/hello-world", "aletheore/docs-update", "abc123", "aletheore[bot]")
    assert [method for method, _ in calls] == ["GET", "POST"]


def test_ensure_branch_at_force_updates_existing_ref_owned_by_us():
    calls = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append((request.method, str(request.url)))
        if request.method == "GET" and str(request.url).endswith("/git/ref/heads/aletheore/docs-update"):
            return httpx.Response(200, json={"object": {"sha": "old-sha"}})
        if request.method == "GET" and str(request.url).endswith("/commits/old-sha"):
            return httpx.Response(200, json={"committer": {"login": "aletheore[bot]"}})
        assert request.method == "PATCH"
        import json as _json
        assert _json.loads(request.content) == {"sha": "new-sha", "force": True}
        return httpx.Response(200, json={})

    client = httpx.Client(transport=httpx.MockTransport(handler), base_url="https://api.github.com")
    ensure_branch_at(client, "token", "octocat/hello-world", "aletheore/docs-update", "new-sha", "aletheore[bot]")
    assert [method for method, _ in calls] == ["GET", "GET", "PATCH"]


def test_ensure_branch_at_refuses_to_force_push_when_not_owned_by_us():
    def handler(request: httpx.Request) -> httpx.Response:
        if request.method == "GET" and str(request.url).endswith("/git/ref/heads/aletheore/docs-update"):
            return httpx.Response(200, json={"object": {"sha": "old-sha"}})
        if request.method == "GET" and str(request.url).endswith("/commits/old-sha"):
            return httpx.Response(200, json={"committer": {"login": "some-contributor"}})
        raise AssertionError(f"unexpected request: {request.method} {request.url}")

    client = httpx.Client(transport=httpx.MockTransport(handler), base_url="https://api.github.com")
    with pytest.raises(BranchNotOwnedByAletheoreError):
        ensure_branch_at(client, "token", "octocat/hello-world", "aletheore/docs-update", "new-sha", "aletheore[bot]")


def test_upsert_repo_file_creates_when_no_existing_file():
    calls = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append((request.method, str(request.url)))
        if request.method == "GET":
            return httpx.Response(404)
        import json as _json
        body = _json.loads(request.content)
        assert "sha" not in body
        assert body["branch"] == "aletheore/docs-update"
        return httpx.Response(201, json={"content": {"sha": "new"}})

    client = httpx.Client(transport=httpx.MockTransport(handler), base_url="https://api.github.com")
    upsert_repo_file(
        client, "token", "octocat/hello-world", ".aletheore/docs/API.md",
        "aletheore/docs-update", "# Docs", "docs: update API reference",
    )
    assert [method for method, _ in calls] == ["GET", "PUT"]


def test_upsert_repo_file_includes_sha_when_file_exists():
    def handler(request: httpx.Request) -> httpx.Response:
        if request.method == "GET":
            return httpx.Response(200, json={"sha": "existing-sha"})
        import json as _json
        assert _json.loads(request.content)["sha"] == "existing-sha"
        return httpx.Response(200, json={"content": {"sha": "updated"}})

    client = httpx.Client(transport=httpx.MockTransport(handler), base_url="https://api.github.com")
    upsert_repo_file(
        client, "token", "octocat/hello-world", ".aletheore/docs/API.md",
        "aletheore/docs-update", "# Docs v2", "docs: update API reference",
    )


def test_find_open_pull_request_returns_number_when_one_exists():
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.params["head"] == "octocat:aletheore/docs-update"
        assert request.url.params["state"] == "open"
        return httpx.Response(200, json=[{"number": 7}])

    client = httpx.Client(transport=httpx.MockTransport(handler), base_url="https://api.github.com")
    assert find_open_pull_request(client, "token", "octocat/hello-world", "aletheore/docs-update") == 7


def test_find_open_pull_request_returns_none_when_no_open_pr():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=[])

    client = httpx.Client(transport=httpx.MockTransport(handler), base_url="https://api.github.com")
    assert find_open_pull_request(client, "token", "octocat/hello-world", "aletheore/docs-update") is None


def test_create_pull_request_returns_new_pr_number():
    def handler(request: httpx.Request) -> httpx.Response:
        import json as _json
        assert _json.loads(request.content) == {
            "title": "docs: update API reference",
            "head": "aletheore/docs-update",
            "base": "main",
            "body": "body",
        }
        return httpx.Response(201, json={"number": 42})

    client = httpx.Client(transport=httpx.MockTransport(handler), base_url="https://api.github.com")
    number = create_pull_request(
        client, "token", "octocat/hello-world", "aletheore/docs-update", "main",
        "docs: update API reference", "body",
    )
    assert number == 42


def test_ensure_docs_pull_request_reuses_existing_open_pr():
    calls = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request.method)
        return httpx.Response(200, json=[{"number": 7}])

    client = httpx.Client(transport=httpx.MockTransport(handler), base_url="https://api.github.com")
    number = ensure_docs_pull_request(
        client, "token", "octocat/hello-world", "aletheore/docs-update", "main", "title", "body",
    )
    assert number == 7
    assert calls == ["GET"]


def test_ensure_docs_pull_request_creates_new_pr_when_none_open():
    def handler(request: httpx.Request) -> httpx.Response:
        if request.method == "GET":
            return httpx.Response(200, json=[])
        return httpx.Response(201, json={"number": 9})

    client = httpx.Client(transport=httpx.MockTransport(handler), base_url="https://api.github.com")
    number = ensure_docs_pull_request(
        client, "token", "octocat/hello-world", "aletheore/docs-update", "main", "title", "body",
    )
    assert number == 9
