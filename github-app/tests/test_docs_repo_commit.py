import httpx
import pytest

from scan_worker.docs_repo_commit import DOCS_COMMIT_BRANCH, DOCS_COMMIT_PATH, sync_docs_to_repo
from scan_worker.github_api import BranchNotOwnedByAletheoreError

MODULES = {"a.py": "# a.py\n\n## Functions\n\n### `f()`\n\nDoes a thing.\n"}
BOT_LOGIN = "aletheore[bot]"


def test_returns_none_when_settings_missing():
    client = httpx.Client(transport=httpx.MockTransport(lambda r: httpx.Response(500)), base_url="https://api.github.com")
    assert sync_docs_to_repo(client, "token", "octocat/hello-world", MODULES, None, BOT_LOGIN) is None


def test_returns_none_when_not_enabled():
    client = httpx.Client(transport=httpx.MockTransport(lambda r: httpx.Response(500)), base_url="https://api.github.com")
    settings = {"enabled": False, "last_content_hash": None, "pr_number": None}
    assert sync_docs_to_repo(client, "token", "octocat/hello-world", MODULES, settings, BOT_LOGIN) is None


def test_returns_none_when_content_unchanged_and_makes_no_network_calls():
    calls = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request.method)
        return httpx.Response(500)

    client = httpx.Client(transport=httpx.MockTransport(handler), base_url="https://api.github.com")
    from aletheore.docs_reference import build_combined_reference
    import hashlib
    current_hash = hashlib.sha256(
        build_combined_reference(MODULES, "octocat/hello-world").encode("utf-8")
    ).hexdigest()
    settings = {"enabled": True, "last_content_hash": current_hash, "pr_number": 3}

    result = sync_docs_to_repo(client, "token", "octocat/hello-world", MODULES, settings, BOT_LOGIN)

    assert result is None
    assert calls == []


def test_pushes_file_and_opens_pr_when_content_changed():
    calls = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append((request.method, str(request.url.path)))
        if request.url.path == "/repos/octocat/hello-world" and request.method == "GET":
            return httpx.Response(200, json={"default_branch": "main"})
        if request.url.path == "/repos/octocat/hello-world/commits/main":
            return httpx.Response(200, json={"sha": "base-sha"})
        if request.url.path == f"/repos/octocat/hello-world/git/ref/heads/{DOCS_COMMIT_BRANCH}":
            return httpx.Response(404)
        if request.url.path == "/repos/octocat/hello-world/git/refs":
            return httpx.Response(201, json={})
        if request.url.path == f"/repos/octocat/hello-world/contents/{DOCS_COMMIT_PATH}":
            if request.method == "GET":
                return httpx.Response(404)
            return httpx.Response(201, json={"content": {"sha": "new"}})
        if request.url.path == "/repos/octocat/hello-world/pulls" and request.method == "GET":
            return httpx.Response(200, json=[])
        if request.url.path == "/repos/octocat/hello-world/pulls" and request.method == "POST":
            return httpx.Response(201, json={"number": 11})
        raise AssertionError(f"unexpected request: {request.method} {request.url.path}")

    client = httpx.Client(transport=httpx.MockTransport(handler), base_url="https://api.github.com")
    settings = {"enabled": True, "last_content_hash": None, "pr_number": None}

    result = sync_docs_to_repo(client, "token", "octocat/hello-world", MODULES, settings, BOT_LOGIN)

    assert result is not None
    content_hash, pr_number = result
    assert pr_number == 11
    assert len(content_hash) == 64  # sha256 hex digest
    assert ("PUT", f"/repos/octocat/hello-world/contents/{DOCS_COMMIT_PATH}") in calls
    assert ("POST", "/repos/octocat/hello-world/pulls") in calls


def test_reuses_existing_open_pr_instead_of_creating_new_one():
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/repos/octocat/hello-world" and request.method == "GET":
            return httpx.Response(200, json={"default_branch": "main"})
        if request.url.path == "/repos/octocat/hello-world/commits/main":
            return httpx.Response(200, json={"sha": "base-sha"})
        if request.url.path == f"/repos/octocat/hello-world/git/ref/heads/{DOCS_COMMIT_BRANCH}":
            return httpx.Response(200, json={"object": {"sha": "old-branch-sha"}})
        if request.url.path == "/repos/octocat/hello-world/commits/old-branch-sha":
            return httpx.Response(200, json={"committer": {"login": BOT_LOGIN}})
        if request.url.path == f"/repos/octocat/hello-world/git/refs/heads/{DOCS_COMMIT_BRANCH}":
            return httpx.Response(200, json={})
        if request.url.path == f"/repos/octocat/hello-world/contents/{DOCS_COMMIT_PATH}":
            if request.method == "GET":
                return httpx.Response(200, json={"sha": "existing-file-sha"})
            return httpx.Response(200, json={"content": {"sha": "updated"}})
        if request.url.path == "/repos/octocat/hello-world/pulls" and request.method == "GET":
            return httpx.Response(200, json=[{"number": 5}])
        raise AssertionError(f"unexpected request: {request.method} {request.url.path}")

    client = httpx.Client(transport=httpx.MockTransport(handler), base_url="https://api.github.com")
    settings = {"enabled": True, "last_content_hash": "stale-hash", "pr_number": 5}

    result = sync_docs_to_repo(client, "token", "octocat/hello-world", MODULES, settings, BOT_LOGIN)

    assert result is not None
    _, pr_number = result
    assert pr_number == 5


def test_refuses_to_force_push_a_branch_it_does_not_own():
    # A branch named DOCS_COMMIT_BRANCH exists but its HEAD commit wasn't
    # made by our bot - force-pushing over it would destroy someone else's
    # work, so this must raise instead of silently resetting the branch.
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/repos/octocat/hello-world" and request.method == "GET":
            return httpx.Response(200, json={"default_branch": "main"})
        if request.url.path == "/repos/octocat/hello-world/commits/main":
            return httpx.Response(200, json={"sha": "base-sha"})
        if request.url.path == f"/repos/octocat/hello-world/git/ref/heads/{DOCS_COMMIT_BRANCH}":
            return httpx.Response(200, json={"object": {"sha": "someone-elses-sha"}})
        if request.url.path == "/repos/octocat/hello-world/commits/someone-elses-sha":
            return httpx.Response(200, json={"committer": {"login": "some-contributor"}})
        raise AssertionError(f"unexpected request: {request.method} {request.url.path}")

    client = httpx.Client(transport=httpx.MockTransport(handler), base_url="https://api.github.com")
    settings = {"enabled": True, "last_content_hash": "stale-hash", "pr_number": None}

    with pytest.raises(BranchNotOwnedByAletheoreError):
        sync_docs_to_repo(client, "token", "octocat/hello-world", MODULES, settings, BOT_LOGIN)
