from unittest.mock import MagicMock

from scan_worker.scheduler import (
    DOCS_CATCHUP_SWEEP_JOB_TIMEOUT_SECONDS,
    HEALTH_QUEUE_NAME,
    HEALTH_SWEEP_JOB_TIMEOUT_SECONDS,
    SCANS_QUEUE_NAME,
    SESSION_CLEANUP_JOB_TIMEOUT_SECONDS,
    WEEKLY_DIGEST_SWEEP_JOB_TIMEOUT_SECONDS,
    run_forever,
)


def test_run_forever_enqueues_health_sweep_and_session_cleanup_on_each_iteration(monkeypatch):
    # Queue() is called once per queue name and reused across iterations -
    # a dict keyed by the name argument stands in for both real Queue
    # instances so scans_queue and health_queue resolve to two distinct
    # fakes instead of collapsing into one.
    fake_queues: dict[str, MagicMock] = {}

    def _fake_queue(name, **kwargs):
        return fake_queues.setdefault(name, MagicMock())

    monkeypatch.setattr("scan_worker.scheduler.Queue", _fake_queue)
    monkeypatch.setattr("scan_worker.scheduler.Redis.from_url", lambda url: MagicMock())
    sleeps = []
    monkeypatch.setattr("scan_worker.scheduler.time.sleep", lambda seconds: sleeps.append(seconds))

    run_forever(interval_seconds=42, max_iterations=3)

    assert set(fake_queues) == {SCANS_QUEUE_NAME, HEALTH_QUEUE_NAME}
    health_queue = fake_queues[HEALTH_QUEUE_NAME]
    scans_queue = fake_queues[SCANS_QUEUE_NAME]

    # The health sweep must never land on the same queue as the AI jobs
    # (Flash review, AIRview, managed audit) below - that's the entire
    # point of the split, so this is checked on the queue object itself
    # rather than just by job name.
    assert health_queue.enqueue.call_count == 3
    for call in health_queue.enqueue.call_args_list:
        assert call.args == ("scan_worker.jobs.run_health_check_sweep_job",)
        assert call.kwargs == {"job_timeout": HEALTH_SWEEP_JOB_TIMEOUT_SECONDS}

    assert scans_queue.enqueue.call_count == 9
    session_cleanup_calls = [
        c for c in scans_queue.enqueue.call_args_list
        if c.args == ("scan_worker.jobs.run_session_cleanup_job",)
    ]
    docs_catchup_calls = [
        c for c in scans_queue.enqueue.call_args_list
        if c.args == ("scan_worker.jobs.run_live_docs_catchup_sweep_job",)
    ]
    weekly_digest_calls = [
        c for c in scans_queue.enqueue.call_args_list
        if c.args == ("scan_worker.jobs.run_weekly_digest_sweep_job",)
    ]
    assert len(session_cleanup_calls) == 3
    assert len(docs_catchup_calls) == 3
    assert len(weekly_digest_calls) == 3
    for call in session_cleanup_calls:
        assert call.kwargs == {"job_timeout": SESSION_CLEANUP_JOB_TIMEOUT_SECONDS}
    for call in docs_catchup_calls:
        assert call.kwargs == {"job_timeout": DOCS_CATCHUP_SWEEP_JOB_TIMEOUT_SECONDS}
    for call in weekly_digest_calls:
        assert call.kwargs == {"job_timeout": WEEKLY_DIGEST_SWEEP_JOB_TIMEOUT_SECONDS}
    # Sleeps between iterations, not after the last one.
    assert sleeps == [42, 42]
