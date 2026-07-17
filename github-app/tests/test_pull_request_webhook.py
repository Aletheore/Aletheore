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
async def test_opened_enqueues_job():
    fake_queue = MagicMock()
    await handle_pull_request_event(_payload("opened"), "redis://unused", queue=fake_queue)
    fake_queue.enqueue.assert_called_once()
    _, kwargs = fake_queue.enqueue.call_args
    assert kwargs["installation_id"] == 111
    assert kwargs["repo_full_name"] == "octocat/hello-world"
    assert kwargs["pr_number"] == 42
    assert kwargs["base_sha"] == "aaa111"
    assert kwargs["head_sha"] == "bbb222"


@pytest.mark.asyncio
async def test_synchronize_enqueues_job():
    fake_queue = MagicMock()
    await handle_pull_request_event(_payload("synchronize"), "redis://unused", queue=fake_queue)
    fake_queue.enqueue.assert_called_once()


@pytest.mark.asyncio
async def test_reopened_enqueues_job():
    fake_queue = MagicMock()
    await handle_pull_request_event(_payload("reopened"), "redis://unused", queue=fake_queue)
    fake_queue.enqueue.assert_called_once()


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
