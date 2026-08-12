import threading
from unittest.mock import MagicMock

from scan_worker.scheduler import (
    DOCS_CATCHUP_SWEEP_JOB_TIMEOUT_SECONDS,
    HEALTH_QUEUE_NAME,
    HEALTH_SWEEP_JOB_TIMEOUT_SECONDS,
    HEALTH_SWEEP_STALENESS_CHECK_JOB_TIMEOUT_SECONDS,
    JOB_TEMP_DIR_CLEANUP_JOB_TIMEOUT_SECONDS,
    OPS_MONITOR_JOB_TIMEOUT_SECONDS,
    SCANS_QUEUE_NAME,
    SESSION_CLEANUP_JOB_TIMEOUT_SECONDS,
    TELEMETRY_CLEANUP_JOB_TIMEOUT_SECONDS,
    WEBHOOK_DELIVERY_CLEANUP_JOB_TIMEOUT_SECONDS,
    WEEKLY_DIGEST_SWEEP_JOB_TIMEOUT_SECONDS,
    WIKI_CATCHUP_SWEEP_JOB_TIMEOUT_SECONDS,
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

    def _record_main_thread_sleep(seconds):
        # scan_worker.scheduler.time IS the global time module, so this patch
        # is global too - and test_heartbeat.py leaves a daemon thread
        # sleeping on a 0.05s loop for the rest of the session. Recording
        # only the main thread keeps this assertion about run_forever's own
        # sleeps instead of whatever else happens to be ticking.
        if threading.current_thread() is threading.main_thread():
            sleeps.append(seconds)

    monkeypatch.setattr("scan_worker.scheduler.time.sleep", _record_main_thread_sleep)

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

    assert scans_queue.enqueue.call_count == 27
    session_cleanup_calls = [
        c for c in scans_queue.enqueue.call_args_list
        if c.args == ("scan_worker.jobs.run_session_cleanup_job",)
    ]
    docs_catchup_calls = [
        c for c in scans_queue.enqueue.call_args_list
        if c.args == ("scan_worker.jobs.run_live_docs_catchup_sweep_job",)
    ]
    wiki_catchup_calls = [
        c for c in scans_queue.enqueue.call_args_list
        if c.args == ("scan_worker.jobs.run_live_wiki_catchup_sweep_job",)
    ]
    weekly_digest_calls = [
        c for c in scans_queue.enqueue.call_args_list
        if c.args == ("scan_worker.jobs.run_weekly_digest_sweep_job",)
    ]
    staleness_check_calls = [
        c for c in scans_queue.enqueue.call_args_list
        if c.args == ("scan_worker.jobs.run_health_sweep_staleness_check_job",)
    ]
    webhook_cleanup_calls = [
        c for c in scans_queue.enqueue.call_args_list
        if c.args == ("scan_worker.jobs.run_webhook_delivery_cleanup_job",)
    ]
    telemetry_cleanup_calls = [
        c for c in scans_queue.enqueue.call_args_list
        if c.args == ("scan_worker.jobs.run_telemetry_cleanup_job",)
    ]
    job_temp_dir_cleanup_calls = [
        c for c in scans_queue.enqueue.call_args_list
        if c.args == ("scan_worker.jobs.run_job_temp_dir_cleanup_job",)
    ]
    ops_monitor_calls = [
        c for c in scans_queue.enqueue.call_args_list
        if c.args == ("scan_worker.jobs.run_ops_monitor_job",)
    ]
    assert len(session_cleanup_calls) == 3
    assert len(docs_catchup_calls) == 3
    assert len(wiki_catchup_calls) == 3
    assert len(weekly_digest_calls) == 3
    assert len(staleness_check_calls) == 3
    assert len(webhook_cleanup_calls) == 3
    assert len(telemetry_cleanup_calls) == 3
    assert len(job_temp_dir_cleanup_calls) == 3
    assert len(ops_monitor_calls) == 3
    for call in session_cleanup_calls:
        assert call.kwargs == {"job_timeout": SESSION_CLEANUP_JOB_TIMEOUT_SECONDS}
    for call in docs_catchup_calls:
        assert call.kwargs == {"job_timeout": DOCS_CATCHUP_SWEEP_JOB_TIMEOUT_SECONDS}
    for call in wiki_catchup_calls:
        assert call.kwargs == {"job_timeout": WIKI_CATCHUP_SWEEP_JOB_TIMEOUT_SECONDS}
    for call in weekly_digest_calls:
        assert call.kwargs == {"job_timeout": WEEKLY_DIGEST_SWEEP_JOB_TIMEOUT_SECONDS}
    for call in staleness_check_calls:
        assert call.kwargs == {"job_timeout": HEALTH_SWEEP_STALENESS_CHECK_JOB_TIMEOUT_SECONDS}
    for call in webhook_cleanup_calls:
        assert call.kwargs == {"job_timeout": WEBHOOK_DELIVERY_CLEANUP_JOB_TIMEOUT_SECONDS}
    for call in telemetry_cleanup_calls:
        assert call.kwargs == {"job_timeout": TELEMETRY_CLEANUP_JOB_TIMEOUT_SECONDS}
    for call in job_temp_dir_cleanup_calls:
        assert call.kwargs == {"job_timeout": JOB_TEMP_DIR_CLEANUP_JOB_TIMEOUT_SECONDS}
    for call in ops_monitor_calls:
        assert call.kwargs == {"job_timeout": OPS_MONITOR_JOB_TIMEOUT_SECONDS}
    # Sleeps between iterations, not after the last one.
    assert sleeps == [42, 42]


def test_run_forever_touches_heartbeat_once_per_completed_iteration(monkeypatch):
    monkeypatch.setattr("scan_worker.scheduler.Queue", lambda name, **kwargs: MagicMock())
    monkeypatch.setattr("scan_worker.scheduler.Redis.from_url", lambda url: MagicMock())
    monkeypatch.setattr("scan_worker.scheduler.time.sleep", lambda seconds: None)
    touches = []
    monkeypatch.setattr("scan_worker.scheduler.touch_heartbeat", lambda: touches.append(True))

    run_forever(interval_seconds=1, max_iterations=3)

    assert len(touches) == 3
