"""The ops monitor emails the operator as the website companion nears and hits its monthly cap."""

from datetime import datetime, timezone

import pytest

from scan_worker import jobs

LEDGER = jobs.COMPANION_LEDGER_KEY
CAP = 500_000_000  # INR 500 in the ledger's micro-INR units
OCTOBER = datetime(2026, 10, 29, 12, 0, tzinfo=timezone.utc).timestamp()
NOVEMBER = datetime(2026, 11, 2, 12, 0, tzinfo=timezone.utc).timestamp()


class _Redis:
    """Just enough redis-py surface for the check; set(nx=True) returns None when the key exists."""

    def __init__(self, ledger=None, as_bytes=False):
        self.ledger = ledger or {}
        self.as_bytes = as_bytes
        self.keys = {}

    def hgetall(self, key):
        assert key == LEDGER
        if self.as_bytes:
            return {k.encode(): str(v).encode() for k, v in self.ledger.items()}
        return dict(self.ledger)

    def get(self, key):
        return self.keys.get(key)

    def set(self, key, value, nx=False, ex=None):
        if nx and key in self.keys:
            return None
        self.keys[key] = value
        return True


def _ledger(used, month=202610, enabled="1", cap=CAP):
    return {
        "version": "1", "enabled": enabled, "day": "20755", "month": str(month),
        "day_used": "0", "month_used": str(used), "day_cap": "20000000", "month_cap": str(cap),
    }


@pytest.fixture
def alerts(monkeypatch):
    sent = []
    monkeypatch.setattr(jobs, "send_error_alert", lambda source, error, context="": sent.append((source, str(error), context)))
    return sent


def test_no_ledger_is_silent(alerts):
    jobs._check_companion_budget(_Redis(), OCTOBER)
    assert alerts == []


def test_below_eighty_percent_is_silent(alerts):
    jobs._check_companion_budget(_Redis(_ledger(used=CAP * 79 // 100)), OCTOBER)
    assert alerts == []


def test_eighty_percent_alerts_once_per_month(alerts):
    redis = _Redis(_ledger(used=CAP * 80 // 100))
    jobs._check_companion_budget(redis, OCTOBER)
    jobs._check_companion_budget(redis, OCTOBER + 180)  # the next ops-monitor cycle
    assert [a[0] for a in alerts] == ["ops_monitor.companion_budget.month_80"]
    assert "passed 80%" in alerts[0][1]
    assert "INR 400.00 of INR 500.00" in alerts[0][2]
    assert "disable" in alerts[0][2]


def test_reaching_the_cap_alerts_separately_and_only_once(alerts):
    redis = _Redis(_ledger(used=CAP * 85 // 100))
    jobs._check_companion_budget(redis, OCTOBER)
    redis.ledger["month_used"] = str(CAP)
    jobs._check_companion_budget(redis, OCTOBER + 180)
    jobs._check_companion_budget(redis, OCTOBER + 360)
    assert [a[0] for a in alerts] == [
        "ops_monitor.companion_budget.month_80",
        "ops_monitor.companion_budget.month_100",
    ]
    assert "REACHED" in alerts[1][1]


def test_jumping_straight_past_the_cap_sends_both_thresholds_once(alerts):
    jobs._check_companion_budget(_Redis(_ledger(used=CAP)), OCTOBER)
    assert [a[0] for a in alerts] == [
        "ops_monitor.companion_budget.month_80",
        "ops_monitor.companion_budget.month_100",
    ]


def test_counters_from_an_earlier_month_do_not_alert(alerts):
    jobs._check_companion_budget(_Redis(_ledger(used=CAP, month=202610)), NOVEMBER)
    assert alerts == []


def test_a_new_month_alerts_again(alerts):
    redis = _Redis(_ledger(used=CAP * 90 // 100))
    jobs._check_companion_budget(redis, OCTOBER)
    redis.ledger = _ledger(used=CAP * 90 // 100, month=202611)
    jobs._check_companion_budget(redis, NOVEMBER)
    assert len(alerts) == 2


def test_disabled_ledger_alerts_through_the_ops_cooldown(alerts):
    redis = _Redis(_ledger(used=0, enabled="0"))
    jobs._check_companion_budget(redis, OCTOBER)
    jobs._check_companion_budget(redis, OCTOBER + 180)
    assert [a[0] for a in alerts] == ["ops_monitor.companion_budget.disabled"]


def test_corrupt_counters_alert(alerts):
    ledger = _ledger(used=0)
    ledger["month_used"] = "not-a-number"
    jobs._check_companion_budget(_Redis(ledger), OCTOBER)
    assert [a[0] for a in alerts] == ["ops_monitor.companion_budget.corrupt"]


def test_bytes_replies_from_redis_are_decoded(alerts):
    jobs._check_companion_budget(_Redis(_ledger(used=CAP * 81 // 100), as_bytes=True), OCTOBER)
    assert [a[0] for a in alerts] == ["ops_monitor.companion_budget.month_80"]


def test_a_failing_check_never_breaks_the_ops_monitor(monkeypatch):
    class Bare:  # no hgetall: the check raises AttributeError
        def get(self, key):
            return None

    monkeypatch.setattr(jobs, "get_redis_client", lambda: Bare())
    for name in ("_check_queue_alerts", "_check_backup_freshness", "_check_webhook_errors"):
        monkeypatch.setattr(jobs, name, lambda *a, **k: None)
    monkeypatch.setattr(jobs, "_check_free_tier_provider_keys", lambda redis_conn: None)
    monkeypatch.setattr(jobs, "_check_app_health", lambda redis_conn, url: None)
    jobs.run_ops_monitor_job()  # must not raise
