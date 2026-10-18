from unittest.mock import MagicMock

import pytest

from app_server.db import hide_repo, set_installation_plan, upsert_installation
from app_server.webhooks import issue_comment
from app_server.webhooks.issue_comment import (
    AUDIT_COMMAND,
    _command_candidate_lines,
    _matches_command,
    handle_issue_comment_event,
)


@pytest.fixture(autouse=True)
def _no_rate_limit(monkeypatch):
    # Every test here shares one hardcoded installation_id (111, see
    # _payload) against a real Redis instance - without this, repeated
    # test runs within the same rate-limit window would accumulate real
    # counts and eventually start failing nondeterministically. The tests
    # that are actually about the limiter override this explicitly.
    monkeypatch.setattr("app_server.webhooks.issue_comment.is_rate_limited", lambda *a, **k: False)


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
async def test_a_word_sharing_the_command_stem_does_not_enqueue(pool, monkeypatch):
    # Real bug found via audit: a bare string-prefix check
    # (line.startswith(AUDIT_COMMAND)) also matches an ordinary English
    # word sharing the same stem - on a GitHub PR thread whose entire
    # subject is reviewing/auditing code, a commenter typing a sentence
    # starting with "audit..." is a real, not hypothetical, risk. This
    # used to enqueue a real, billed, AIR-tier-gated managed audit with
    # no intent to trigger it.
    await _seed_paid_installation(pool)
    _mock_permission_check(monkeypatch, "write")
    fake_queue = MagicMock()
    await handle_issue_comment_event(
        _payload("/aletheore auditing this PR now"), pool, "redis://unused", queue=fake_queue
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
async def test_audit_command_on_a_flash_plan_installation_does_not_enqueue(pool, monkeypatch):
    # Real, live gap found during a full-session final audit: this gate
    # used to check "== free", which the $6/mo flash plan (a real value
    # once #468 shipped) passed straight through - flash does not include
    # managed audits, same exclusion as AIRview/Docs/the managed
    # dashboard, but any write-access commenter on a flash repo could
    # still trigger a full, real, meaningfully-more-expensive-than-a-PR-
    # review managed-audit run via this ChatOps command.
    await upsert_installation(pool, 111, "octocat")
    await set_installation_plan(pool, 111, "flash")
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


@pytest.mark.asyncio
async def test_audit_command_on_a_hidden_repo_does_not_reach_the_permission_check(pool, monkeypatch):
    # A repo the customer deselected from the installation - GitHub access
    # is already revoked, so stay quiet rather than spend a GitHub API
    # round trip verifying a commenter's permission on it.
    await _seed_paid_installation(pool)
    await hide_repo(pool, 111, "octocat/hello-world")
    permission_check = MagicMock()
    monkeypatch.setattr(
        "app_server.webhooks.issue_comment.get_repo_permission_for_user", permission_check
    )
    fake_queue = MagicMock()
    await handle_issue_comment_event(_payload("/aletheore audit"), pool, "redis://unused", queue=fake_queue)
    permission_check.assert_not_called()
    fake_queue.enqueue.assert_not_called()


@pytest.mark.asyncio
async def test_audit_command_over_the_rate_limit_does_not_reach_the_permission_check(pool, monkeypatch):
    # Real gap found via audit: verifying a commenter's permission is 2 real
    # outbound GitHub API calls, charged against the installation's shared
    # quota, reachable by ANY commenter on a public repo - there is no
    # token to revoke the way there is for the API endpoints. A tripped
    # limit must stop the 2 API calls, not just the eventual enqueue.
    await _seed_paid_installation(pool)
    monkeypatch.setattr("app_server.webhooks.issue_comment.is_rate_limited", lambda *a, **k: True)
    permission_check = MagicMock()
    monkeypatch.setattr(
        "app_server.webhooks.issue_comment.get_repo_permission_for_user", permission_check
    )
    fake_queue = MagicMock()
    await handle_issue_comment_event(_payload("/aletheore audit"), pool, "redis://unused", queue=fake_queue)
    permission_check.assert_not_called()
    fake_queue.enqueue.assert_not_called()


@pytest.mark.asyncio
async def test_audit_command_rate_limit_is_keyed_by_installation_and_commenter(pool, monkeypatch):
    # Real gap found by Flash Review: an installation-only key let any
    # commenter (no access needed) drain the whole installation's shared
    # budget before the permission check ever ran, silently locking out a
    # legitimate maintainer's own attempt right after. Per-commenter keying
    # means an attacker can only ever exhaust their own bucket.
    await _seed_paid_installation(pool)
    _mock_permission_check(monkeypatch, "write")
    keys = []
    monkeypatch.setattr(
        "app_server.webhooks.issue_comment.is_rate_limited",
        lambda redis_conn, key, limit, window: keys.append(key) or False,
    )
    fake_queue = MagicMock()
    await handle_issue_comment_event(
        _payload("/aletheore audit", commenter="someuser"), pool, "redis://unused", queue=fake_queue
    )
    assert keys == ["ratelimit:comment-command:111:someuser"]


@pytest.mark.asyncio
async def test_audit_command_rate_limit_does_not_lock_out_a_different_commenter(pool, monkeypatch):
    # The actual scenario Flash Review caught: attacker spams 20 comments
    # under their own login first, then a real maintainer comments the
    # same command - the maintainer's own bucket must be untouched.
    await _seed_paid_installation(pool)
    _mock_permission_check(monkeypatch, "write")

    from app_server import rate_limit as real_rate_limit

    class _FakeRedis:
        def __init__(self):
            self.counts: dict[str, int] = {}

        def pipeline(self):
            return _FakePipeline(self)

    class _FakePipeline:
        def __init__(self, redis):
            self.redis = redis
            self.ops = []

        def incr(self, key):
            self.ops.append(("incr", key))

        def expire(self, key, window_seconds, nx=True):
            self.ops.append(("expire", key))

        def execute(self):
            results = []
            for op, key in self.ops:
                if op == "incr":
                    self.redis.counts[key] = self.redis.counts.get(key, 0) + 1
                    results.append(self.redis.counts[key])
                else:
                    results.append(True)
            return results

    fake_redis = _FakeRedis()
    monkeypatch.setattr("app_server.webhooks.issue_comment.is_rate_limited", real_rate_limit.is_rate_limited)
    monkeypatch.setattr("app_server.redis_client.get_redis_client", lambda: fake_redis)

    # Attacker drains their own bucket.
    for _ in range(issue_comment.COMMENT_COMMAND_RATE_LIMIT + 1):
        await handle_issue_comment_event(
            _payload("/aletheore audit", commenter="attacker"), pool, "redis://unused", queue=MagicMock()
        )

    fake_queue = MagicMock()
    await handle_issue_comment_event(
        _payload("/aletheore audit", commenter="maintainer"), pool, "redis://unused", queue=fake_queue
    )

    fake_queue.enqueue.assert_called_once()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "body",
    [
        "To run it comment:\n```\n/aletheore audit\n```",
        "To run it comment:\n~~~\n/aletheore audit\n~~~",
        "Example:\n\n    /aletheore audit\n",
    ],
)
async def test_command_inside_a_code_block_does_not_enqueue(pool, monkeypatch, body):
    await _seed_paid_installation(pool)
    _mock_permission_check(monkeypatch, "write")
    fake_queue = MagicMock()
    await handle_issue_comment_event(_payload(body), pool, "redis://unused", queue=fake_queue)
    fake_queue.enqueue.assert_not_called()


def test_command_inside_a_shorter_nested_fence_is_not_a_candidate():
    # Real bug found via audit: the fence-close check compared only the
    # first 3 characters of a line, ignoring fence length. Per CommonMark/
    # GFM (how GitHub itself renders the comment), a closing fence must be
    # the same character and >= the opening fence's length - a literal
    # ``` line inside a ````-opened (4-backtick) fence does not close it.
    # The old check treated any 3-of-the-same-char prefix as a valid
    # closer, ending fence tracking early and exposing the command below
    # it as unfenced, which fired a real, billed, AIR-tier-gated managed
    # audit from a comment whose command was, visually and per GitHub's
    # own rendering, still inside the code block.
    body = "````\n```\n/aletheore audit\n````"
    assert list(_command_candidate_lines(body)) == []
    assert not any(_matches_command(line, AUDIT_COMMAND) for line in _command_candidate_lines(body))


@pytest.mark.asyncio
async def test_audit_command_inside_a_shorter_nested_fence_does_not_enqueue(pool, monkeypatch):
    await _seed_paid_installation(pool)
    _mock_permission_check(monkeypatch, "write")
    fake_queue = MagicMock()
    await handle_issue_comment_event(
        _payload("````\n```\n/aletheore audit\n````"), pool, "redis://unused", queue=fake_queue
    )
    fake_queue.enqueue.assert_not_called()


@pytest.mark.asyncio
async def test_command_after_a_closed_code_block_still_enqueues(pool, monkeypatch):
    await _seed_paid_installation(pool)
    _mock_permission_check(monkeypatch, "write")
    fake_queue = MagicMock()
    await handle_issue_comment_event(
        _payload("```\nfoo\n```\n/aletheore audit"), pool, "redis://unused", queue=fake_queue
    )
    fake_queue.enqueue.assert_called_once()
