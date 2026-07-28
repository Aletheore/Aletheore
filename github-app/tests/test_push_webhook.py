from unittest.mock import MagicMock

import pytest

from app_server.webhooks.push import handle_push_event


def _payload(ref="refs/heads/main", default_branch="main", deleted=False, after="head123", commits=None):
    return {
        "ref": ref,
        "after": after,
        "deleted": deleted,
        "installation": {"id": 111},
        "repository": {"full_name": "octocat/hello-world", "default_branch": default_branch},
        "commits": commits
        if commits is not None
        else [
            {"added": ["new.py"], "removed": [], "modified": ["app.py"]},
            {"added": [], "removed": ["old.py"], "modified": ["app.py"]},
        ],
    }


@pytest.mark.asyncio
async def test_push_to_default_branch_enqueues_scan_job():
    fake_queue = MagicMock()
    await handle_push_event(_payload(), "redis://unused", queue=fake_queue)

    fake_queue.enqueue.assert_called_once()
    args, kwargs = fake_queue.enqueue.call_args
    assert args[0] == "scan_worker.jobs.run_push_scan_job"
    assert kwargs["installation_id"] == 111
    assert kwargs["repo_full_name"] == "octocat/hello-world"
    assert kwargs["head_sha"] == "head123"
    assert kwargs["changed_files"] == ["app.py", "new.py", "old.py"]
    assert kwargs["job_timeout"] > 0


@pytest.mark.asyncio
async def test_push_to_non_default_branch_does_not_enqueue():
    fake_queue = MagicMock()
    await handle_push_event(
        _payload(ref="refs/heads/feature-x", default_branch="main"), "redis://unused", queue=fake_queue
    )
    fake_queue.enqueue.assert_not_called()


@pytest.mark.asyncio
async def test_branch_deletion_does_not_enqueue():
    fake_queue = MagicMock()
    await handle_push_event(_payload(deleted=True), "redis://unused", queue=fake_queue)
    fake_queue.enqueue.assert_not_called()


@pytest.mark.asyncio
async def test_push_with_no_file_changes_enqueues_empty_changed_files():
    fake_queue = MagicMock()
    await handle_push_event(_payload(commits=[]), "redis://unused", queue=fake_queue)

    fake_queue.enqueue.assert_called_once()
    _, kwargs = fake_queue.enqueue.call_args
    assert kwargs["changed_files"] == []
