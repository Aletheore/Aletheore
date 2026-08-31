import asyncio
import os
from contextlib import contextmanager

import asyncpg
from rq import Queue

from app_server.webhooks.pull_request import handle_pull_request_event

TEST_DATABASE_URL = os.environ.get(
    "TEST_DATABASE_URL",
    "postgresql://postgres:test@localhost:55433/aletheore_test",
)


@contextmanager
def _noop_repo_checkout_lock(*args, **kwargs):
    yield


def test_pull_request_webhook_to_pr_comment_end_to_end(
    bare_repo_with_two_commits, redis_conn, pool, monkeypatch
):
    """Every other webhook/job test mocks the RQ queue entirely (a
    MagicMock captures the enqueue call) or calls run_pr_scan_job directly
    with hand-written kwargs - neither would catch the webhook handler's
    enqueue() kwargs drifting out of sync with run_pr_scan_job's real
    parameter names. This test enqueues onto a real Redis-backed queue and
    executes the resulting Job the way RQ's own worker does (Job.perform()),
    so a signature mismatch would raise here instead of only in production.
    """
    bare_path, base_sha, head_sha = bare_repo_with_two_commits
    posted = {}

    def fake_upsert(client, token, repo_full_name, pr_number, body):
        posted["body"] = body
        posted["repo_full_name"] = repo_full_name
        posted["pr_number"] = pr_number

    monkeypatch.setenv("DATABASE_URL", "postgresql://unused")
    monkeypatch.setattr("scan_worker.jobs.get_installation_row", lambda *a, **k: None)
    monkeypatch.setattr(
        "scan_worker.jobs.get_dismissed_identity_keys",
        lambda *a, **k: {"secret": set(), "vulnerability": set()},
    )
    monkeypatch.setattr("scan_worker.jobs.upsert_pr_comment", fake_upsert)
    # This test deliberately makes no other GitHub API mock - real
    # enqueue-through-perform, per its own docstring. fetch_pr_is_open is
    # the one real network call run_pr_scan_job now makes before cloning
    # anything; "fake-token" below isn't a real GitHub token, so an
    # unmocked call here would 401, same as any other real call would.
    monkeypatch.setattr("scan_worker.jobs.fetch_pr_is_open", lambda *a, **k: True)
    monkeypatch.setattr("scan_worker.jobs._clone_url", lambda repo_full_name, token: bare_path)
    monkeypatch.setattr("scan_worker.jobs.get_installation_token", lambda *a, **k: "fake-token")
    monkeypatch.setattr("scan_worker.jobs.generate_app_jwt", lambda *a, **k: "fake-jwt")
    monkeypatch.setattr("scan_worker.jobs._insert_history", lambda *a, **k: None)
    monkeypatch.setattr("scan_worker.jobs._maybe_send_slack_alert", lambda *a, **k: None)
    monkeypatch.setattr("scan_worker.jobs._maybe_create_check_run", lambda *a, **k: None)
    # repo_checkout_lock opens a real psycopg connection to
    # settings.database_url - the fake "postgresql://unused" DSN set above
    # would hang or fail slowly rather than exercising the actual lock.
    monkeypatch.setattr("scan_worker.jobs.repo_checkout_lock", _noop_repo_checkout_lock)

    payload = {
        "action": "opened",
        "number": 7,
        "installation": {"id": 1},
        "repository": {"full_name": "octocat/hello-world"},
        "pull_request": {"base": {"sha": base_sha}, "head": {"sha": head_sha}},
    }

    queue = Queue("scans", connection=redis_conn)

    async def _dispatch():
        # A fresh pool, not the `pool` fixture's - that pool is bound to
        # pytest-asyncio's own event loop, and asyncpg pools can't be used
        # from a different loop than the one they were created on (surfaces
        # as "another operation is in progress" / a closed-connection
        # error). handle_pull_request_event is async, but the job it
        # enqueues resolves its GitHub token via asyncio.run() internally
        # (see scan_worker.jobs._token_sync), which raises if called from
        # inside an already-running event loop - so this whole dispatch
        # (pool included) has to live inside its own asyncio.run(), scoped
        # to just the enqueue call and closed before .perform() runs the
        # job, exactly as happens in production (webhook request handling
        # and job execution are different processes, never sharing a loop).
        dispatch_pool = await asyncpg.create_pool(TEST_DATABASE_URL)
        try:
            await handle_pull_request_event(payload, dispatch_pool, "unused", queue=queue)
        finally:
            await dispatch_pool.close()

    asyncio.run(_dispatch())

    pr_scan_jobs = [j for j in queue.jobs if j.func_name == "scan_worker.jobs.run_pr_scan_job"]
    assert len(pr_scan_jobs) == 1

    pr_scan_jobs[0].perform()

    assert "Secrets" in posted["body"]
    assert posted["repo_full_name"] == "octocat/hello-world"
    assert posted["pr_number"] == 7
