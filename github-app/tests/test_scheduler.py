import threading
from types import SimpleNamespace
from unittest.mock import MagicMock

from scan_worker.scheduler import (
    BUILDS_QUEUE_NAME,
    DOCS_CATCHUP_SWEEP_JOB_TIMEOUT_SECONDS,
    ENDPOINT_HEALTH_CLEANUP_JOB_TIMEOUT_SECONDS,
    EVIDENCE_PACKET_CACHE_CLEANUP_JOB_TIMEOUT_SECONDS,
    FLASH_REVIEW_CACHE_CLEANUP_JOB_TIMEOUT_SECONDS,
    HEALTH_QUEUE_NAME,
    HEALTH_SWEEP_JOB_TIMEOUT_SECONDS,
    HEALTH_SWEEP_STALENESS_CHECK_JOB_TIMEOUT_SECONDS,
    JOB_TEMP_DIR_CLEANUP_JOB_TIMEOUT_SECONDS,
    LLM_SPEND_RESERVATION_SWEEP_JOB_TIMEOUT_SECONDS,
    MONTHLY_CREDIT_RESET_JOB_TIMEOUT_SECONDS,
    OPS_MONITOR_JOB_TIMEOUT_SECONDS,
    SCANS_QUEUE_NAME,
    SESSION_CLEANUP_JOB_TIMEOUT_SECONDS,
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

    assert set(fake_queues) == {SCANS_QUEUE_NAME, HEALTH_QUEUE_NAME, BUILDS_QUEUE_NAME}
    health_queue = fake_queues[HEALTH_QUEUE_NAME]
    scans_queue = fake_queues[SCANS_QUEUE_NAME]
    # The two catch-up sweeps run up to 1800s each (they call the full-build jobs, which are real LLM calls),
    # so they live on their own queue: on the shared one, a pair of them held both scan workers for half an
    # hour and starved every PR review (2026-10-30).
    builds_queue = fake_queues[BUILDS_QUEUE_NAME]

    # The health sweep must never land on the same queue as the AI jobs
    # (Flash review, AIRview, managed audit) below - that's the entire
    # point of the split, so this is checked on the queue object itself
    # rather than just by job name.
    assert health_queue.enqueue.call_count == 3
    for call in health_queue.enqueue.call_args_list:
        assert call.args == ("scan_worker.jobs.run_health_check_sweep_job",)
        assert call.kwargs == {"job_timeout": HEALTH_SWEEP_JOB_TIMEOUT_SECONDS}

    assert scans_queue.enqueue.call_count == 33
    assert builds_queue.enqueue.call_count == 6
    session_cleanup_calls = [
        c for c in scans_queue.enqueue.call_args_list
        if c.args == ("scan_worker.jobs.run_session_cleanup_job",)
    ]
    docs_catchup_calls = [
        c for c in builds_queue.enqueue.call_args_list
        if c.args == ("scan_worker.jobs.run_live_docs_catchup_sweep_job",)
    ]
    wiki_catchup_calls = [
        c for c in builds_queue.enqueue.call_args_list
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
    job_temp_dir_cleanup_calls = [
        c for c in scans_queue.enqueue.call_args_list
        if c.args == ("scan_worker.jobs.run_job_temp_dir_cleanup_job",)
    ]
    endpoint_health_cleanup_calls = [
        c for c in scans_queue.enqueue.call_args_list
        if c.args == ("scan_worker.jobs.run_endpoint_health_cleanup_job",)
    ]
    flash_review_cache_cleanup_calls = [
        c for c in scans_queue.enqueue.call_args_list
        if c.args == ("scan_worker.jobs.run_flash_review_cache_cleanup_job",)
    ]
    evidence_packet_cache_cleanup_calls = [
        c for c in scans_queue.enqueue.call_args_list
        if c.args == ("scan_worker.jobs.run_evidence_packet_cache_cleanup_job",)
    ]
    llm_spend_reservation_sweep_calls = [
        c for c in scans_queue.enqueue.call_args_list
        if c.args == ("scan_worker.jobs.run_llm_spend_reservation_sweep_job",)
    ]
    ops_monitor_calls = [
        c for c in scans_queue.enqueue.call_args_list
        if c.args == ("scan_worker.jobs.run_ops_monitor_job",)
    ]
    monthly_credit_reset_calls = [
        c for c in scans_queue.enqueue.call_args_list
        if c.args == ("scan_worker.jobs.run_monthly_credit_reset_sweep_job",)
    ]
    assert len(session_cleanup_calls) == 3
    assert len(docs_catchup_calls) == 3
    assert len(wiki_catchup_calls) == 3
    assert len(weekly_digest_calls) == 3
    assert len(staleness_check_calls) == 3
    assert len(webhook_cleanup_calls) == 3
    assert len(job_temp_dir_cleanup_calls) == 3
    assert len(endpoint_health_cleanup_calls) == 3
    assert len(flash_review_cache_cleanup_calls) == 3
    assert len(evidence_packet_cache_cleanup_calls) == 3
    assert len(llm_spend_reservation_sweep_calls) == 3
    assert len(ops_monitor_calls) == 3
    # The synthetic monthly credit reset for annual subscribers has to be
    # enqueued every tick like the rest: it is the only thing that gives an
    # annual AIR customer their monthly allotment (see
    # run_monthly_credit_reset_sweep_job), so a scheduler that silently
    # stopped enqueueing it would under-credit them for a year.
    assert len(monthly_credit_reset_calls) == 3
    for call in monthly_credit_reset_calls:
        assert call.kwargs == {"job_timeout": MONTHLY_CREDIT_RESET_JOB_TIMEOUT_SECONDS}
    for call in endpoint_health_cleanup_calls:
        assert call.kwargs == {"job_timeout": ENDPOINT_HEALTH_CLEANUP_JOB_TIMEOUT_SECONDS}
    for call in flash_review_cache_cleanup_calls:
        assert call.kwargs == {"job_timeout": FLASH_REVIEW_CACHE_CLEANUP_JOB_TIMEOUT_SECONDS}
    for call in evidence_packet_cache_cleanup_calls:
        assert call.kwargs == {"job_timeout": EVIDENCE_PACKET_CACHE_CLEANUP_JOB_TIMEOUT_SECONDS}
    for call in llm_spend_reservation_sweep_calls:
        assert call.kwargs == {"job_timeout": LLM_SPEND_RESERVATION_SWEEP_JOB_TIMEOUT_SECONDS}
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


class _FakeJob:
    def __init__(self, func_name):
        self.func_name = func_name


class _FakeQueue:
    """Just enough of rq.Queue for the scheduler's "what is already pending" check."""

    def __init__(self, queued=(), started=()):
        self.jobs = [_FakeJob(name) for name in queued]
        self._started = {f"started-{i}": _FakeJob(name) for i, name in enumerate(started)}
        self.enqueue = MagicMock()

    def fetch_job(self, job_id):
        return self._started.get(job_id)


def _run_one_tick(monkeypatch, scans, builds, started_registry=None):
    queues = {SCANS_QUEUE_NAME: scans, BUILDS_QUEUE_NAME: builds, HEALTH_QUEUE_NAME: _FakeQueue()}
    monkeypatch.setattr("scan_worker.scheduler.Queue", lambda name, **kwargs: queues[name])
    monkeypatch.setattr("scan_worker.scheduler.Redis.from_url", lambda url: MagicMock())
    monkeypatch.setattr("scan_worker.scheduler.time.sleep", lambda seconds: None)
    monkeypatch.setattr("scan_worker.scheduler.touch_heartbeat", lambda: None)
    monkeypatch.setattr(
        "scan_worker.scheduler.StartedJobRegistry",
        started_registry
        or (lambda queue: SimpleNamespace(get_job_ids=lambda: list(queue._started))),
    )
    run_forever(interval_seconds=1, max_iterations=1)


def _enqueued_names(queue):
    return [call.args[0] for call in queue.enqueue.call_args_list]


def test_run_forever_puts_the_long_catchup_sweeps_on_the_builds_queue_only(monkeypatch):
    scans, builds = _FakeQueue(), _FakeQueue()

    _run_one_tick(monkeypatch, scans, builds)

    assert sorted(_enqueued_names(builds)) == [
        "scan_worker.jobs.run_live_docs_catchup_sweep_job",
        "scan_worker.jobs.run_live_wiki_catchup_sweep_job",
    ]
    assert "scan_worker.jobs.run_live_docs_catchup_sweep_job" not in _enqueued_names(scans)
    assert "scan_worker.jobs.run_live_wiki_catchup_sweep_job" not in _enqueued_names(scans)


def test_run_forever_does_not_enqueue_a_job_that_is_already_queued_or_running(monkeypatch):
    # Prod 2026-10-30: two 1800s sweeps held both workers, and the scheduler kept adding a fresh copy of every
    # housekeeping job each 180s tick regardless - 10 stacked copies of each type, queue depth 154. A job
    # still waiting or running must not be enqueued again; the next tick will do it once it has finished.
    scans = _FakeQueue(
        queued=["scan_worker.jobs.run_session_cleanup_job"] * 3,
        started=["scan_worker.jobs.run_ops_monitor_job"],
    )
    builds = _FakeQueue(
        queued=["scan_worker.jobs.run_live_docs_catchup_sweep_job"],
        started=["scan_worker.jobs.run_live_wiki_catchup_sweep_job"],
    )

    _run_one_tick(monkeypatch, scans, builds)

    scans_enqueued = _enqueued_names(scans)
    assert "scan_worker.jobs.run_session_cleanup_job" not in scans_enqueued
    assert "scan_worker.jobs.run_ops_monitor_job" not in scans_enqueued
    assert len(scans_enqueued) == 9  # the other jobs are unaffected
    assert _enqueued_names(builds) == []


def test_run_forever_still_enqueues_everything_when_the_pending_check_fails(monkeypatch):
    # Same fail-open stance as the rest of the scheduler's helpers: not being able to look at the queue must
    # never mean housekeeping silently stops (the monthly credit reset, for one, is only ever enqueued here).
    scans, builds = _FakeQueue(), _FakeQueue()

    def _broken_registry(queue):
        raise ConnectionError("redis unavailable")

    _run_one_tick(monkeypatch, scans, builds, started_registry=_broken_registry)

    assert len(_enqueued_names(scans)) == 11
    assert len(_enqueued_names(builds)) == 2
