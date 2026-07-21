from unittest.mock import MagicMock

from scan_worker.scheduler import run_forever


def test_run_forever_enqueues_health_sweep_on_each_iteration(monkeypatch):
    fake_queue = MagicMock()
    monkeypatch.setattr("scan_worker.scheduler.Queue", lambda *a, **k: fake_queue)
    monkeypatch.setattr("scan_worker.scheduler.Redis.from_url", lambda url: MagicMock())
    sleeps = []
    monkeypatch.setattr("scan_worker.scheduler.time.sleep", lambda seconds: sleeps.append(seconds))

    run_forever(interval_seconds=42, max_iterations=3)

    assert fake_queue.enqueue.call_count == 3
    for call in fake_queue.enqueue.call_args_list:
        assert call.args == ("scan_worker.jobs.run_health_check_sweep_job",)
    # Sleeps between iterations, not after the last one.
    assert sleeps == [42, 42]
