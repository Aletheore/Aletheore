import base64

import httpx

from aletheore.pr_comment import COMMENT_MARKER
from scan_worker.github_api import (
    create_check_run,
    fetch_file_content,
    fetch_pr_changed_files,
    fetch_pr_diff,
    fetch_recent_commits_for_path,
    upsert_pr_comment,
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
