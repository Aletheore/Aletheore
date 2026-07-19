from unittest.mock import MagicMock

import pytest

from app_server.webhooks.issue_comment import handle_issue_comment_event


def _payload(comment_body: str, has_pr: bool = True):
    payload = {
        "action": "created",
        "installation": {"id": 111},
        "repository": {"full_name": "octocat/hello-world"},
        "issue": {"number": 42},
        "comment": {"body": comment_body},
    }
    if has_pr:
        payload["issue"]["pull_request"] = {"url": "https://api.github.com/..."}
    return payload


@pytest.mark.asyncio
async def test_audit_command_enqueues_managed_audit_job():
    fake_queue = MagicMock()
    await handle_issue_comment_event(_payload("/aletheore audit"), "redis://unused", queue=fake_queue)
    fake_queue.enqueue.assert_called_once()
    args, kwargs = fake_queue.enqueue.call_args
    assert args[0] == "scan_worker.jobs.run_managed_audit_pr_job"
    assert kwargs["installation_id"] == 111
    assert kwargs["repo_full_name"] == "octocat/hello-world"
    assert kwargs["pr_number"] == 42
    # RQ's default job timeout (~180s) is too short for a real LLM-backed audit
    # call - a real run was killed mid-flight by this before job_timeout was set.
    assert kwargs["job_timeout"] >= 600


@pytest.mark.asyncio
async def test_non_audit_comment_does_not_enqueue():
    fake_queue = MagicMock()
    await handle_issue_comment_event(_payload("regular comment"), "redis://unused", queue=fake_queue)
    fake_queue.enqueue.assert_not_called()


@pytest.mark.asyncio
async def test_comment_on_plain_issue_not_pr_does_not_enqueue():
    fake_queue = MagicMock()
    await handle_issue_comment_event(
        _payload("/aletheore audit", has_pr=False),
        "redis://unused",
        queue=fake_queue,
    )
    fake_queue.enqueue.assert_not_called()
