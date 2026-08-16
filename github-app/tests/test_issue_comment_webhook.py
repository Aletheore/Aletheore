from unittest.mock import MagicMock

import pytest

from app_server.db import upsert_installation, set_installation_plan
from app_server.webhooks.issue_comment import handle_issue_comment_event


def _payload(comment_body: str, has_pr: bool = True, commenter: str = "someuser"):
    payload = {
        "action": "created",
        "installation": {"id": 111},
        "repository": {"full_name": "octocat/hello-world"},
        "issue": {"number": 42},
        "comment": {"body": comment_body, "user": {"login": commenter}},
    }
    if has_pr:
        payload["issue"]["pull_request"] = {"url": "https://api.github.com/..."}
    return payload


def _mock_permission_check(monkeypatch, permission: str | None, raises: bool = False):
    monkeypatch.setattr(
        "app_server.webhooks.issue_comment.generate_app_jwt", lambda *a, **k: "fake-jwt"
    )
    monkeypatch.setattr(
        "app_server.webhooks.issue_comment.get_installation_token", MagicMock(return_value="fake-token")
    )
    if raises:

        def _raise(*a, **k):
            raise RuntimeError("GitHub API unavailable")

        monkeypatch.setattr("app_server.webhooks.issue_comment.get_repo_permission_for_user", _raise)
    else:
        monkeypatch.setattr(
            "app_server.webhooks.issue_comment.get_repo_permission_for_user",
            lambda *a, **k: permission,
        )


async def _seed_paid_installation(pool):
    # Managed audits (what the /aletheore audit ChatOps trigger enqueues)
    # are a paid feature - every test below that expects an enqueue needs
    # a real paid installation row, or the plan gate rejects it before the
    # permission check the test is actually trying to exercise ever runs.
    await upsert_installation(pool, 111, "octocat")
    await set_installation_plan(pool, 111, "air")


@pytest.mark.asyncio
async def test_audit_command_enqueues_managed_audit_job_for_a_write_collaborator(pool, monkeypatch):
    await _seed_paid_installation(pool)
    _mock_permission_check(monkeypatch, "write")
    fake_queue = MagicMock()
    await handle_issue_comment_event(_payload("/aletheore audit"), pool, "redis://unused", queue=fake_queue)
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
async def test_audit_command_enqueues_for_an_admin_too(pool, monkeypatch):
    await _seed_paid_installation(pool)
    _mock_permission_check(monkeypatch, "admin")
    fake_queue = MagicMock()
    await handle_issue_comment_event(_payload("/aletheore audit"), pool, "redis://unused", queue=fake_queue)
    fake_queue.enqueue.assert_called_once()


@pytest.mark.asyncio
async def test_audit_command_from_a_read_only_commenter_does_not_enqueue(pool, monkeypatch):
    await _seed_paid_installation(pool)
    _mock_permission_check(monkeypatch, "read")
    fake_queue = MagicMock()
    await handle_issue_comment_event(_payload("/aletheore audit"), pool, "redis://unused", queue=fake_queue)
    fake_queue.enqueue.assert_not_called()


@pytest.mark.asyncio
async def test_audit_command_from_a_non_collaborator_does_not_enqueue(pool, monkeypatch):
    await _seed_paid_installation(pool)
    _mock_permission_check(monkeypatch, "none")
    fake_queue = MagicMock()
    await handle_issue_comment_event(_payload("/aletheore audit"), pool, "redis://unused", queue=fake_queue)
    fake_queue.enqueue.assert_not_called()


@pytest.mark.asyncio
async def test_audit_command_does_not_enqueue_when_permission_check_itself_fails(pool, monkeypatch):
    # Fails closed: an API error while verifying the commenter's permission
    # must not be treated as authorization to proceed.
    await _seed_paid_installation(pool)
    _mock_permission_check(monkeypatch, None, raises=True)
    fake_queue = MagicMock()
    await handle_issue_comment_event(_payload("/aletheore audit"), pool, "redis://unused", queue=fake_queue)
    fake_queue.enqueue.assert_not_called()


@pytest.mark.asyncio
async def test_non_audit_comment_does_not_enqueue(pool, monkeypatch):
    await _seed_paid_installation(pool)
    _mock_permission_check(monkeypatch, "write")
    fake_queue = MagicMock()
    await handle_issue_comment_event(_payload("regular comment"), pool, "redis://unused", queue=fake_queue)
    fake_queue.enqueue.assert_not_called()


@pytest.mark.asyncio
async def test_quoted_command_does_not_enqueue(pool, monkeypatch):
    await _seed_paid_installation(pool)
    _mock_permission_check(monkeypatch, "write")
    fake_queue = MagicMock()
    await handle_issue_comment_event(
        _payload("Please do not run /aletheore audit here"), pool, "redis://unused", queue=fake_queue
    )
    fake_queue.enqueue.assert_not_called()


@pytest.mark.asyncio
async def test_bot_command_does_not_enqueue(pool, monkeypatch):
    await _seed_paid_installation(pool)
    _mock_permission_check(monkeypatch, "write")
    fake_queue = MagicMock()
    payload = _payload("/aletheore audit")
    payload["comment"]["user"]["type"] = "Bot"
    await handle_issue_comment_event(payload, pool, "redis://unused", queue=fake_queue)
    fake_queue.enqueue.assert_not_called()


@pytest.mark.asyncio
async def test_comment_on_plain_issue_not_pr_does_not_enqueue(pool, monkeypatch):
    await _seed_paid_installation(pool)
    _mock_permission_check(monkeypatch, "write")
    fake_queue = MagicMock()
    await handle_issue_comment_event(
        _payload("/aletheore audit", has_pr=False),
        pool,
        "redis://unused",
        queue=fake_queue,
    )
    fake_queue.enqueue.assert_not_called()


@pytest.mark.asyncio
async def test_audit_command_on_a_free_plan_installation_does_not_enqueue(pool, monkeypatch):
    # Managed audits require a paid plan (see managed_audit_api.py's own
    # 402 for the HTTP trigger) - this ChatOps trigger previously had no
    # equivalent gate at all, letting any repo with write/admin access
    # (trivially self-granted by installing the free app on your own
    # repo) run unlimited clone+scan cycles on the shared scans queue.
    await upsert_installation(pool, 111, "octocat")  # defaults to plan='free'
    _mock_permission_check(monkeypatch, "write")
    fake_queue = MagicMock()
    await handle_issue_comment_event(_payload("/aletheore audit"), pool, "redis://unused", queue=fake_queue)
    fake_queue.enqueue.assert_not_called()


@pytest.mark.asyncio
async def test_audit_command_with_no_installation_row_does_not_enqueue(pool, monkeypatch):
    _mock_permission_check(monkeypatch, "write")
    fake_queue = MagicMock()
    await handle_issue_comment_event(_payload("/aletheore audit"), pool, "redis://unused", queue=fake_queue)
    fake_queue.enqueue.assert_not_called()


@pytest.mark.asyncio
async def test_audit_command_on_a_free_plan_installation_never_reaches_the_permission_check(pool, monkeypatch):
    # The plan gate is checked first and is cheap (one DB read) - a free
    # installation shouldn't cost a GitHub API round trip to reject.
    await upsert_installation(pool, 111, "octocat")
    permission_check = MagicMock()
    monkeypatch.setattr(
        "app_server.webhooks.issue_comment.get_repo_permission_for_user", permission_check
    )
    fake_queue = MagicMock()
    await handle_issue_comment_event(_payload("/aletheore audit"), pool, "redis://unused", queue=fake_queue)
    permission_check.assert_not_called()
    fake_queue.enqueue.assert_not_called()
