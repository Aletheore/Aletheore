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


def _pr(**fields):
    return {"base": {"sha": "aaa111"}, "head": {"sha": "bbb222"}, "merged": False, **fields}


@pytest.mark.parametrize("login", ["dependabot[bot]", "renovate[bot]", "pre-commit-ci[bot]", "snyk-bot", "Dependabot[bot]"])
def test_dependency_bots_get_no_flash_review(login):
    from app_server.webhooks.pull_request import flash_review_skip_reason

    assert flash_review_skip_reason(_pr(user={"login": login}, title="Bump x from 1 to 2")) == f"opened by {login.lower()}"


@pytest.mark.parametrize("login", ["octocat", "copilot-swe-agent[bot]", "claude[bot]", "github-actions[bot]", ""])
def test_humans_and_ai_coding_agents_are_still_reviewed(login):
    # "Bot" is not a reason to skip: AI agents that open pull requests are bots too, and are worth reviewing.
    from app_server.webhooks.pull_request import flash_review_skip_reason

    assert flash_review_skip_reason(_pr(user={"login": login}, title="Add a feature")) is None


@pytest.mark.parametrize("title", ["[skip review] wip: spike", "Tidy imports [No Review]", "chore [SKIP ALETHEORE]"])
def test_title_markers_skip_the_review_case_insensitively(title):
    from app_server.webhooks.pull_request import flash_review_skip_reason

    assert flash_review_skip_reason(_pr(user={"login": "octocat"}, title=title)) is not None


def test_a_skip_label_skips_and_the_force_label_overrides_every_skip_rule():
    from app_server.webhooks.pull_request import flash_review_skip_reason

    assert flash_review_skip_reason(_pr(user={"login": "octocat"}, labels=[{"name": "skip-review"}])) == "labelled skip-review"
    # a maintainer edited the Dependabot branch by hand and wants it reviewed
    forced = _pr(user={"login": "dependabot[bot]"}, title="[skip review] x", labels=[{"name": "Aletheore-Review"}, {"name": "no-review"}])
    assert flash_review_skip_reason(forced) is None


def test_a_payload_missing_every_field_is_reviewed_not_skipped():
    from app_server.webhooks.pull_request import flash_review_skip_reason

    assert flash_review_skip_reason({}) is None
    assert flash_review_skip_reason({"user": None, "title": None, "labels": None}) is None
    assert flash_review_skip_reason({"labels": ["not-a-dict"]}) is None


@pytest.mark.asyncio
async def test_a_dependabot_pull_request_runs_the_deterministic_scan_but_no_flash_review(pool):
    payload = _payload("opened")
    payload["pull_request"]["user"] = {"login": "dependabot[bot]"}
    fake_queue = MagicMock()

    await handle_pull_request_event(payload, pool, "redis://unused", queue=fake_queue)

    assert [call.args[0] for call in fake_queue.enqueue.call_args_list] == ["scan_worker.jobs.run_pr_scan_job"]


@pytest.mark.asyncio
async def test_a_human_pull_request_still_gets_both_jobs(pool):
    payload = _payload("synchronize")
    payload["pull_request"]["user"] = {"login": "octocat"}
    fake_queue = MagicMock()

    await handle_pull_request_event(payload, pool, "redis://unused", queue=fake_queue)

    assert fake_queue.enqueue.call_count == 2


def test_a_draft_pull_request_gets_no_flash_review():
    from app_server.webhooks.pull_request import flash_review_skip_reason

    assert flash_review_skip_reason(_pr(user={"login": "octocat"}, draft=True)) == "draft pull request"


@pytest.mark.parametrize("draft", [False, None, "true", 1, "yes"])
def test_only_an_explicit_true_draft_flag_skips(draft):
    # A missing or odd value must review. Skipping is only ever triggered by something positively present.
    from app_server.webhooks.pull_request import flash_review_skip_reason

    payload = _pr(user={"login": "octocat"})
    if draft is not None:
        payload["draft"] = draft
    assert flash_review_skip_reason(payload) is None


def test_the_force_label_reviews_a_draft():
    from app_server.webhooks.pull_request import flash_review_skip_reason

    assert flash_review_skip_reason(_pr(user={"login": "octocat"}, draft=True, labels=[{"name": "aletheore-review"}])) is None


@pytest.mark.asyncio
@pytest.mark.parametrize("action", ["opened", "synchronize", "reopened"])
async def test_a_draft_pull_request_runs_the_scan_but_no_flash_review(pool, action):
    payload = _payload(action)
    payload["pull_request"]["draft"] = True
    fake_queue = MagicMock()

    await handle_pull_request_event(payload, pool, "redis://unused", queue=fake_queue)

    assert [call.args[0] for call in fake_queue.enqueue.call_args_list] == ["scan_worker.jobs.run_pr_scan_job"]


@pytest.mark.asyncio
async def test_marking_a_draft_ready_queues_the_flash_review_and_not_a_second_scan(pool):
    # The scan already ran on every push to the draft and the head has not changed since.
    payload = _payload("ready_for_review")
    payload["pull_request"]["draft"] = False
    fake_queue = MagicMock()

    await handle_pull_request_event(payload, pool, "redis://unused", queue=fake_queue)

    assert [call.args[0] for call in fake_queue.enqueue.call_args_list] == ["scan_worker.jobs.run_flash_review_job"]
    kwargs = fake_queue.enqueue.call_args_list[0].kwargs
    assert kwargs["pr_number"] == 42 and kwargs["head_sha"] == "bbb222" and kwargs["base_sha"] == "aaa111"
    assert kwargs["job_timeout"] >= 600


@pytest.mark.asyncio
async def test_marking_a_dependency_bot_pull_request_ready_still_skips_the_review(pool):
    payload = _payload("ready_for_review")
    payload["pull_request"]["user"] = {"login": "dependabot[bot]"}
    fake_queue = MagicMock()

    await handle_pull_request_event(payload, pool, "redis://unused", queue=fake_queue)

    assert fake_queue.enqueue.call_count == 0


@pytest.mark.asyncio
async def test_converting_to_draft_does_nothing(pool):
    fake_queue = MagicMock()
    await handle_pull_request_event(_payload("converted_to_draft"), pool, "redis://unused", queue=fake_queue)
    assert fake_queue.enqueue.call_count == 0


@pytest.mark.asyncio
async def test_marking_ready_in_a_hidden_repo_enqueues_nothing(pool):
    await upsert_installation(pool, 111, "octocat")
    await hide_repo(pool, 111, "octocat/hello-world")
    fake_queue = MagicMock()

    await handle_pull_request_event(_payload("ready_for_review"), pool, "redis://unused", queue=fake_queue)

    assert fake_queue.enqueue.call_count == 0
