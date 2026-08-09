from unittest.mock import MagicMock

import pytest

from app_server.webhooks.pull_request import handle_pull_request_event


def _payload(action: str):
    return {
        "action": action,
        "number": 42,
        "installation": {"id": 111},
        "repository": {"full_name": "octocat/hello-world"},
        "pull_request": {
            "base": {"sha": "aaa111"},
            "head": {"sha": "bbb222"},
        },
    }


@pytest.mark.asyncio
async def test_opened_enqueues_both_jobs():
    fake_queue = MagicMock()
    await handle_pull_request_event(_payload("opened"), "redis://unused", queue=fake_queue)
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
async def test_flash_review_job_timeout_has_real_margin():
    # A real flash review (LLM call + several sequential GitHub API
    # fetches) measured at 5m50s in production against a ~10-file diff -
    # the previous 180s value silently killed most non-trivial reviews
    # via RQ's work-horse SIGKILL watchdog (a hard kill the job's own
    # except block never sees), leaving no error and no comment. This
    # guards against that regressing back to something too tight.
    fake_queue = MagicMock()
    await handle_pull_request_event(_payload("opened"), "redis://unused", queue=fake_queue)
    calls_by_job = {call.args[0]: call.kwargs for call in fake_queue.enqueue.call_args_list}
    assert calls_by_job["scan_worker.jobs.run_flash_review_job"]["job_timeout"] >= 600


@pytest.mark.asyncio
async def test_synchronize_enqueues_both_jobs():
    fake_queue = MagicMock()
    await handle_pull_request_event(_payload("synchronize"), "redis://unused", queue=fake_queue)
    assert fake_queue.enqueue.call_count == 2


@pytest.mark.asyncio
async def test_reopened_enqueues_both_jobs():
    fake_queue = MagicMock()
    await handle_pull_request_event(_payload("reopened"), "redis://unused", queue=fake_queue)
    assert fake_queue.enqueue.call_count == 2


@pytest.mark.asyncio
async def test_closed_does_not_enqueue():
    fake_queue = MagicMock()
    await handle_pull_request_event(_payload("closed"), "redis://unused", queue=fake_queue)
    fake_queue.enqueue.assert_not_called()


@pytest.mark.asyncio
async def test_labeled_does_not_enqueue():
    fake_queue = MagicMock()
    await handle_pull_request_event(_payload("labeled"), "redis://unused", queue=fake_queue)
    fake_queue.enqueue.assert_not_called()
