import httpx

from aletheore.pr_comment import COMMENT_MARKER
from scan_worker.github_api import upsert_pr_comment


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
