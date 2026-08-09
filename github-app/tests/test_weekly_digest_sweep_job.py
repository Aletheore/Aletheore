from scan_worker.jobs import run_weekly_digest_sweep_job


def _patch_installation_data(monkeypatch, *, account_login="acme", emails=("alice@example.com",)):
    monkeypatch.setattr("scan_worker.jobs.get_installation_row", lambda dsn, iid: {"account_login": account_login})
    monkeypatch.setattr("scan_worker.jobs.count_repo_scans_since", lambda dsn, iid, since: 3)
    monkeypatch.setattr("scan_worker.jobs.get_llm_spend_this_month", lambda dsn, iid: 5.5)
    monkeypatch.setattr("scan_worker.jobs.get_flash_review_count_this_month", lambda dsn, iid: 2)
    monkeypatch.setattr(
        "scan_worker.jobs.get_endpoint_health_summary", lambda dsn, iid: {"total": 3, "reachable": 3}
    )
    monkeypatch.setattr("scan_worker.jobs.list_installation_member_emails", lambda dsn, iid: list(emails))


def test_processes_each_due_installation_and_enqueues_per_member(monkeypatch):
    monkeypatch.setattr("scan_worker.jobs.list_paid_installations_due_for_digest", lambda dsn, interval: [1])
    _patch_installation_data(monkeypatch, emails=("alice@example.com", "bob@example.com"))

    enqueue_calls = []
    monkeypatch.setattr(
        "scan_worker.jobs.enqueue_transactional_email",
        lambda redis_url, **kwargs: enqueue_calls.append(kwargs),
    )
    recorded = []
    monkeypatch.setattr("scan_worker.jobs.record_digest_sent", lambda dsn, iid: recorded.append(iid))

    run_weekly_digest_sweep_job()

    assert len(enqueue_calls) == 2
    for call in enqueue_calls:
        assert call["template_name"] == "weekly_digest"
        assert call["installation_id"] == 1
        assert call["template_arg"] == {
            "account_login": "acme",
            "scans_this_week": 3,
            "llm_spend_month_to_date": 5.5,
            "flash_reviews_month_to_date": 2,
            "endpoints_reachable": 3,
            "endpoints_total": 3,
        }
    emails = {c["to_email"] for c in enqueue_calls}
    assert emails == {"alice@example.com", "bob@example.com"}
    dedupe_keys = {c["dedupe_key"] for c in enqueue_calls}
    assert all(k.startswith("weekly_digest:1:") for k in dedupe_keys)
    # Distinct recipients must get distinct dedupe keys, or only one of
    # them would ever actually receive the send.
    assert len(dedupe_keys) == 2

    assert recorded == [1]


def test_missing_installation_is_skipped_without_error(monkeypatch):
    monkeypatch.setattr("scan_worker.jobs.list_paid_installations_due_for_digest", lambda dsn, interval: [1])
    monkeypatch.setattr("scan_worker.jobs.get_installation_row", lambda dsn, iid: None)

    def _fail_if_called(*a, **k):
        raise AssertionError("should not enqueue for a missing installation")

    monkeypatch.setattr("scan_worker.jobs.enqueue_transactional_email", _fail_if_called)

    run_weekly_digest_sweep_job()  # must not raise


def test_one_installation_failing_does_not_stop_the_others(monkeypatch):
    monkeypatch.setattr("scan_worker.jobs.list_paid_installations_due_for_digest", lambda dsn, interval: [1, 2])

    def _get_installation(dsn, iid):
        if iid == 1:
            raise RuntimeError("boom")
        return {"account_login": "acme-2"}

    monkeypatch.setattr("scan_worker.jobs.get_installation_row", _get_installation)
    monkeypatch.setattr("scan_worker.jobs.count_repo_scans_since", lambda dsn, iid, since: 0)
    monkeypatch.setattr("scan_worker.jobs.get_llm_spend_this_month", lambda dsn, iid: 0.0)
    monkeypatch.setattr("scan_worker.jobs.get_flash_review_count_this_month", lambda dsn, iid: 0)
    monkeypatch.setattr(
        "scan_worker.jobs.get_endpoint_health_summary", lambda dsn, iid: {"total": 0, "reachable": 0}
    )
    monkeypatch.setattr("scan_worker.jobs.list_installation_member_emails", lambda dsn, iid: ["m@example.com"])

    enqueue_calls = []
    monkeypatch.setattr(
        "scan_worker.jobs.enqueue_transactional_email",
        lambda redis_url, **kwargs: enqueue_calls.append(kwargs),
    )
    monkeypatch.setattr("scan_worker.jobs.record_digest_sent", lambda dsn, iid: None)

    run_weekly_digest_sweep_job()  # installation 1 fails, installation 2 still processed

    assert len(enqueue_calls) == 1
    assert enqueue_calls[0]["template_arg"]["account_login"] == "acme-2"


def test_no_installations_due_enqueues_nothing(monkeypatch):
    monkeypatch.setattr("scan_worker.jobs.list_paid_installations_due_for_digest", lambda dsn, interval: [])

    def _fail_if_called(*a, **k):
        raise AssertionError("should not enqueue when nothing is due")

    monkeypatch.setattr("scan_worker.jobs.enqueue_transactional_email", _fail_if_called)

    run_weekly_digest_sweep_job()
