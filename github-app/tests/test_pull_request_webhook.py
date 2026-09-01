from unittest.mock import MagicMock

import httpx
import pytest

from app_server.db import hide_repo, upsert_installation
from app_server.webhooks.pull_request import handle_pull_request_event


def _payload(action: str, merged: bool = False):
    return {
        "action": action,
        "number": 42,
        "installation": {"id": 111},
        "repository": {"full_name": "octocat/hello-world"},
        "pull_request": {
            "base": {"sha": "aaa111"},
            "head": {"sha": "bbb222"},
            "merged": merged,
        },
    }


@pytest.mark.asyncio
async def test_opened_enqueues_both_jobs(pool):
    fake_queue = MagicMock()
    await handle_pull_request_event(_payload("opened"), pool, "redis://unused", queue=fake_queue)
    assert fake_queue.enqueue.call_count == 2
    job_names = {call.args[0] for call in fake_queue.enqueue.call_args_list}
    assert job_names == {"scan_worker.jobs.run_pr_scan_job", "scan_worker.jobs.run_flash_review_job"}
    for call in fake_queue.enqueue.call_args_list:
        _, kwargs = call
        assert kwargs["installation_id"] == 111
        assert kwargs["repo_full_name"] == "octocat/hello-world"
        assert kwargs["pr_number"] == 42
        assert kwargs["base_sha"] == "aaa111"
        assert kwargs["head_sha"] == "bbb222"
        assert kwargs["job_timeout"] > 0


@pytest.mark.asyncio
async def test_flash_review_job_timeout_has_real_margin(pool):
    # A real flash review (LLM call + several sequential GitHub API
    # fetches) measured at 5m50s in production against a ~10-file diff -
    # the previous 180s value silently killed most non-trivial reviews
    # via RQ's work-horse SIGKILL watchdog (a hard kill the job's own
    # except block never sees), leaving no error and no comment. This
    # guards against that regressing back to something too tight.
    fake_queue = MagicMock()
    await handle_pull_request_event(_payload("opened"), pool, "redis://unused", queue=fake_queue)
    calls_by_job = {call.args[0]: call.kwargs for call in fake_queue.enqueue.call_args_list}
    assert calls_by_job["scan_worker.jobs.run_flash_review_job"]["job_timeout"] >= 600


@pytest.mark.asyncio
async def test_synchronize_enqueues_both_jobs(pool):
    fake_queue = MagicMock()
    await handle_pull_request_event(_payload("synchronize"), pool, "redis://unused", queue=fake_queue)
    assert fake_queue.enqueue.call_count == 2


@pytest.mark.asyncio
async def test_reopened_enqueues_both_jobs(pool):
    fake_queue = MagicMock()
    await handle_pull_request_event(_payload("reopened"), pool, "redis://unused", queue=fake_queue)
    assert fake_queue.enqueue.call_count == 2


@pytest.mark.asyncio
async def test_closed_and_merged_does_not_enqueue(pool):
    # A merge fires its own `push` event on the default branch (see
    # handle_push_event), which already reconciles AIRview against the
    # real merged code - nothing more to do here.
    fake_queue = MagicMock()
    await handle_pull_request_event(_payload("closed", merged=True), pool, "redis://unused", queue=fake_queue)
    fake_queue.enqueue.assert_not_called()


@pytest.mark.asyncio
async def test_closed_without_merge_corrects_the_wiki_against_the_real_default_branch(pool, monkeypatch):
    # Real bug this closes: a PR closed WITHOUT merging fires no push at
    # all, so nothing ever re-scanned the real default branch to correct
    # AIRview's wiki, which run_pr_scan_job already updated straight off
    # this PR's own proposed, now-abandoned head. Without this, the wiki
    # (and every other get_latest_evidence reader) kept describing that
    # abandoned PR's content indefinitely.
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/repos/octocat/hello-world":
            return httpx.Response(200, json={"default_branch": "main"})
        if request.url.path == "/repos/octocat/hello-world/commits/main":
            return httpx.Response(200, json={"sha": "real-main-head"})
        assert request.url.path == "/repos/octocat/hello-world/compare/aaa111...bbb222"
        return httpx.Response(200, json={"files": [{"filename": "app.py"}, {"filename": "lib.py"}]})

    client = httpx.Client(transport=httpx.MockTransport(handler), base_url="https://api.github.com")
    monkeypatch.setattr("app_server.webhooks.pull_request.generate_app_jwt", lambda *a, **k: "app-jwt")
    monkeypatch.setattr(
        "app_server.webhooks.pull_request.get_installation_token",
        lambda installation_id, app_jwt: "installation-token",
    )
    monkeypatch.setattr("app_server.webhooks.pull_request.get_github_api_client", lambda: client)

    fake_queue = MagicMock()
    await handle_pull_request_event(_payload("closed", merged=False), pool, "redis://unused", queue=fake_queue)

    fake_queue.enqueue.assert_called_once()
    args, kwargs = fake_queue.enqueue.call_args
    assert args[0] == "scan_worker.jobs.run_push_scan_job"
    assert kwargs["installation_id"] == 111
    assert kwargs["repo_full_name"] == "octocat/hello-world"
    assert kwargs["head_sha"] == "real-main-head"
    assert kwargs["changed_files"] == ["app.py", "lib.py"]
    assert kwargs["job_timeout"] > 0


@pytest.mark.asyncio
async def test_closed_without_merge_and_no_commits_on_default_branch_does_not_enqueue(pool, monkeypatch):
    # fetch_default_branch_head_sha returns None for a repo with no commits
    # yet on its default branch (a real, normal state, not an error) -
    # nothing to correct against.
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/repos/octocat/hello-world":
            return httpx.Response(200, json={"default_branch": "main"})
        assert request.url.path == "/repos/octocat/hello-world/commits/main"
        return httpx.Response(409, json={"message": "Git Repository is empty."})

    client = httpx.Client(transport=httpx.MockTransport(handler), base_url="https://api.github.com")
    monkeypatch.setattr("app_server.webhooks.pull_request.generate_app_jwt", lambda *a, **k: "app-jwt")
    monkeypatch.setattr(
        "app_server.webhooks.pull_request.get_installation_token",
        lambda installation_id, app_jwt: "installation-token",
    )
    monkeypatch.setattr("app_server.webhooks.pull_request.get_github_api_client", lambda: client)

    fake_queue = MagicMock()
    await handle_pull_request_event(_payload("closed", merged=False), pool, "redis://unused", queue=fake_queue)

    fake_queue.enqueue.assert_not_called()


@pytest.mark.asyncio
async def test_labeled_does_not_enqueue(pool):
    fake_queue = MagicMock()
    await handle_pull_request_event(_payload("labeled"), pool, "redis://unused", queue=fake_queue)
    fake_queue.enqueue.assert_not_called()


@pytest.mark.asyncio
async def test_hidden_repo_does_not_enqueue_anything(pool):
    # A repo the customer deselected from the installation - GitHub access
    # is already revoked, so a PR event for it must be a clean no-op, not
    # a scan job that starts and fails partway through.
    await upsert_installation(pool, 111, "octocat")
    await hide_repo(pool, 111, "octocat/hello-world")
    fake_queue = MagicMock()
    await handle_pull_request_event(_payload("opened"), pool, "redis://unused", queue=fake_queue)
    fake_queue.enqueue.assert_not_called()
