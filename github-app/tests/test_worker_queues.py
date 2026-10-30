from scan_worker.worker import queue_names_from_env


def test_a_worker_serves_scans_then_builds_by_default(monkeypatch):
    # "scans" first: RQ takes jobs from the queues in the order given, so a worker that is free always
    # prefers a waiting review over a waiting build. An unconfigured deploy keeps serving builds too.
    monkeypatch.delenv("SCAN_WORKER_QUEUES", raising=False)

    assert queue_names_from_env() == ["scans", "builds"]


def test_a_replica_can_be_restricted_to_scans_only(monkeypatch):
    # Without this a pair of 30-minute builds could occupy every replica and starve PR reviews, which is
    # what happened on 2026-10-30. One replica serving only "scans" keeps review capacity free.
    monkeypatch.setenv("SCAN_WORKER_QUEUES", "scans")

    assert queue_names_from_env() == ["scans"]


def test_queue_names_keep_the_order_given_and_ignore_whitespace(monkeypatch):
    monkeypatch.setenv("SCAN_WORKER_QUEUES", " builds , scans ,")

    assert queue_names_from_env() == ["builds", "scans"]


def test_a_blank_setting_falls_back_to_the_default(monkeypatch):
    # An empty value in a compose file must not leave a worker that listens to nothing.
    monkeypatch.setenv("SCAN_WORKER_QUEUES", " , ")

    assert queue_names_from_env() == ["scans", "builds"]
