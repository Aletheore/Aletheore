from unittest.mock import MagicMock

import pytest

from app_server.db import hide_repo, upsert_installation
from app_server.dismissed_findings import get_dismissed_identity_keys
from app_server.webhooks.pull_request_review_comment import handle_pull_request_review_comment_event


def _payload(
    body: str,
    in_reply_to_id: int | None = 555,
    commenter: str = "someuser",
    commenter_type: str = "User",
    installation_id: int = 111,
    repo_full_name: str = "octocat/hello-world",
):
    comment = {"body": body, "user": {"login": commenter, "type": commenter_type}}
    if in_reply_to_id is not None:
        comment["in_reply_to_id"] = in_reply_to_id
    return {
        "action": "created",
        "installation": {"id": installation_id},
        "repository": {"full_name": repo_full_name},
        "comment": comment,
    }


def _mock_permission_check(monkeypatch, permission: str | None, raises: bool = False):
    monkeypatch.setattr(
        "app_server.webhooks.pull_request_review_comment.generate_app_jwt", lambda *a, **k: "fake-jwt"
    )
    monkeypatch.setattr(
        "app_server.webhooks.pull_request_review_comment.get_installation_token",
        MagicMock(return_value="fake-token"),
    )
    if raises:

        def _raise(*a, **k):
            raise RuntimeError("GitHub API unavailable")

        monkeypatch.setattr(
            "app_server.webhooks.pull_request_review_comment.get_repo_permission_for_user", _raise
        )
    else:
        monkeypatch.setattr(
            "app_server.webhooks.pull_request_review_comment.get_repo_permission_for_user",
            lambda *a, **k: permission,
        )


async def _seed_installation(pool, installation_id: int = 111, account_login: str = "octocat"):
    await upsert_installation(pool, installation_id, account_login)


async def _seed_tracked_comment(
    pool,
    installation_id: int = 111,
    repo_full_name: str = "octocat/hello-world",
    pr_number: int = 42,
    finding_type: str = "flash_review_llm",
    identity_key: str = "app.py\x1f10\x1fabc123",
    github_comment_id: int = 555,
):
    await pool.execute(
        """
        INSERT INTO flash_review_finding_comments
            (installation_id, repo_full_name, pr_number, finding_type, identity_key,
             github_comment_id, last_seen_sha)
        VALUES ($1, $2, $3, $4, $5, $6, 'deadbeef')
        """,
        installation_id,
        repo_full_name,
        pr_number,
        finding_type,
        identity_key,
        github_comment_id,
    )


@pytest.mark.asyncio
async def test_dismiss_reply_from_a_write_collaborator_records_dismissal(pool, monkeypatch):
    await _seed_installation(pool)
    await _seed_tracked_comment(pool)
    _mock_permission_check(monkeypatch, "write")

    await handle_pull_request_review_comment_event(_payload("/dismiss"), pool, "redis://unused")

    dismissed = await get_dismissed_identity_keys(pool, 111, "octocat/hello-world")
    assert "app.py\x1f10\x1fabc123" in dismissed["flash_review_llm"]


@pytest.mark.asyncio
async def test_dismiss_reply_from_an_admin_also_records(pool, monkeypatch):
    await _seed_installation(pool)
    await _seed_tracked_comment(pool)
    _mock_permission_check(monkeypatch, "admin")

    await handle_pull_request_review_comment_event(_payload("/dismiss"), pool, "redis://unused")

    dismissed = await get_dismissed_identity_keys(pool, 111, "octocat/hello-world")
    assert "app.py\x1f10\x1fabc123" in dismissed["flash_review_llm"]


@pytest.mark.asyncio
async def test_dismiss_reply_captures_the_reason_text(pool, monkeypatch):
    await _seed_installation(pool)
    await _seed_tracked_comment(pool)
    _mock_permission_check(monkeypatch, "write")

    await handle_pull_request_review_comment_event(
        _payload("/dismiss this is a false positive"), pool, "redis://unused"
    )

    row = await pool.fetchrow(
        "SELECT reason FROM dismissed_findings WHERE installation_id = $1 AND identity_key = $2",
        111,
        "app.py\x1f10\x1fabc123",
    )
    assert row["reason"] == "this is a false positive"


@pytest.mark.asyncio
async def test_dismiss_reply_from_a_read_only_commenter_does_not_record(pool, monkeypatch):
    await _seed_installation(pool)
    await _seed_tracked_comment(pool)
    _mock_permission_check(monkeypatch, "read")

    await handle_pull_request_review_comment_event(_payload("/dismiss"), pool, "redis://unused")

    dismissed = await get_dismissed_identity_keys(pool, 111, "octocat/hello-world")
    assert dismissed["flash_review_llm"] == set()


@pytest.mark.asyncio
async def test_dismiss_reply_from_a_non_collaborator_does_not_record(pool, monkeypatch):
    await _seed_installation(pool)
    await _seed_tracked_comment(pool)
    _mock_permission_check(monkeypatch, "none")

    await handle_pull_request_review_comment_event(_payload("/dismiss"), pool, "redis://unused")

    dismissed = await get_dismissed_identity_keys(pool, 111, "octocat/hello-world")
    assert dismissed["flash_review_llm"] == set()


@pytest.mark.asyncio
async def test_dismiss_reply_does_not_record_when_permission_check_itself_fails(pool, monkeypatch):
    # Fails closed: an API error while verifying the commenter's permission
    # must not be treated as authorization to proceed.
    await _seed_installation(pool)
    await _seed_tracked_comment(pool)
    _mock_permission_check(monkeypatch, None, raises=True)

    await handle_pull_request_review_comment_event(_payload("/dismiss"), pool, "redis://unused")

    dismissed = await get_dismissed_identity_keys(pool, 111, "octocat/hello-world")
    assert dismissed["flash_review_llm"] == set()


@pytest.mark.asyncio
async def test_non_reply_comment_does_not_record(pool, monkeypatch):
    # in_reply_to_id absent - a top-level review comment, not a reply.
    await _seed_installation(pool)
    await _seed_tracked_comment(pool)
    _mock_permission_check(monkeypatch, "write")

    await handle_pull_request_review_comment_event(
        _payload("/dismiss", in_reply_to_id=None), pool, "redis://unused"
    )

    dismissed = await get_dismissed_identity_keys(pool, 111, "octocat/hello-world")
    assert dismissed["flash_review_llm"] == set()


@pytest.mark.asyncio
async def test_bot_reply_does_not_record(pool, monkeypatch):
    # Never act on our own resolution-edit comments or any other bot reply.
    await _seed_installation(pool)
    await _seed_tracked_comment(pool)
    _mock_permission_check(monkeypatch, "write")

    await handle_pull_request_review_comment_event(
        _payload("/dismiss", commenter_type="Bot"), pool, "redis://unused"
    )

    dismissed = await get_dismissed_identity_keys(pool, 111, "octocat/hello-world")
    assert dismissed["flash_review_llm"] == set()


@pytest.mark.asyncio
async def test_reply_without_dismiss_command_does_not_record(pool, monkeypatch):
    await _seed_installation(pool)
    await _seed_tracked_comment(pool)
    _mock_permission_check(monkeypatch, "write")

    await handle_pull_request_review_comment_event(
        _payload("thanks, fixing this now"), pool, "redis://unused"
    )

    dismissed = await get_dismissed_identity_keys(pool, 111, "octocat/hello-world")
    assert dismissed["flash_review_llm"] == set()


@pytest.mark.asyncio
async def test_reply_to_an_untracked_comment_does_not_record_and_does_not_crash(pool, monkeypatch):
    # in_reply_to_id doesn't match any row in flash_review_finding_comments
    # - a reply to some other, non-Flash-Review review comment.
    await _seed_installation(pool)
    _mock_permission_check(monkeypatch, "write")

    await handle_pull_request_review_comment_event(
        _payload("/dismiss", in_reply_to_id=999999), pool, "redis://unused"
    )

    dismissed = await get_dismissed_identity_keys(pool, 111, "octocat/hello-world")
    assert dismissed["flash_review_llm"] == set()


@pytest.mark.asyncio
async def test_untracked_comment_never_reaches_the_permission_check(pool, monkeypatch):
    await _seed_installation(pool)
    permission_check = MagicMock()
    monkeypatch.setattr(
        "app_server.webhooks.pull_request_review_comment.get_repo_permission_for_user", permission_check
    )

    await handle_pull_request_review_comment_event(
        _payload("/dismiss", in_reply_to_id=999999), pool, "redis://unused"
    )

    permission_check.assert_not_called()


@pytest.mark.asyncio
async def test_dismiss_reply_on_a_hidden_repo_does_not_reach_the_permission_check(pool, monkeypatch):
    await _seed_installation(pool)
    await _seed_tracked_comment(pool)
    await hide_repo(pool, 111, "octocat/hello-world")
    permission_check = MagicMock()
    monkeypatch.setattr(
        "app_server.webhooks.pull_request_review_comment.get_repo_permission_for_user", permission_check
    )

    await handle_pull_request_review_comment_event(_payload("/dismiss"), pool, "redis://unused")

    permission_check.assert_not_called()


@pytest.mark.asyncio
async def test_tracked_comment_belonging_to_a_different_installation_is_refused(pool, monkeypatch):
    # in_reply_to_id is a GitHub-wide comment id, not scoped to one
    # installation - a payload claiming installation 111 must not act on a
    # tracked row that actually belongs to installation 222.
    await _seed_installation(pool, installation_id=111, account_login="octocat")
    await _seed_installation(pool, installation_id=222, account_login="someorg")
    await _seed_tracked_comment(pool, installation_id=222, repo_full_name="someorg/other-repo")
    _mock_permission_check(monkeypatch, "write")

    await handle_pull_request_review_comment_event(
        _payload("/dismiss", installation_id=111, repo_full_name="octocat/hello-world"),
        pool,
        "redis://unused",
    )

    dismissed_111 = await get_dismissed_identity_keys(pool, 111, "octocat/hello-world")
    dismissed_222 = await get_dismissed_identity_keys(pool, 222, "someorg/other-repo")
    assert dismissed_111["flash_review_llm"] == set()
    assert dismissed_222["flash_review_llm"] == set()


@pytest.mark.asyncio
async def test_dismiss_reply_only_affects_the_specific_finding_type_replied_to(pool, monkeypatch):
    # Same identity_key can exist under both flash_review_llm and
    # flash_review_semantic (see dismissed_findings.py's docstring on why
    # a raw-key collision across the two types is harmless) - dismissing
    # one must not dismiss the other.
    await _seed_installation(pool)
    await _seed_tracked_comment(
        pool, finding_type="flash_review_llm", identity_key="shared\x1fkey", github_comment_id=1
    )
    await _seed_tracked_comment(
        pool, finding_type="flash_review_semantic", identity_key="shared\x1fkey", github_comment_id=2
    )
    _mock_permission_check(monkeypatch, "write")

    await handle_pull_request_review_comment_event(
        _payload("/dismiss", in_reply_to_id=1), pool, "redis://unused"
    )

    dismissed = await get_dismissed_identity_keys(pool, 111, "octocat/hello-world")
    assert "shared\x1fkey" in dismissed["flash_review_llm"]
    assert "shared\x1fkey" not in dismissed["flash_review_semantic"]
