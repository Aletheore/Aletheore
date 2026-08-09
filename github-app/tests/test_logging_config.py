import json
import logging

import pytest

from app_server.logging_config import JsonFormatter, log_job


def _record(msg: str, **extra) -> logging.LogRecord:
    record = logging.LogRecord(
        name="test.logger",
        level=logging.INFO,
        pathname=__file__,
        lineno=1,
        msg=msg,
        args=(),
        exc_info=None,
    )
    for key, value in extra.items():
        setattr(record, key, value)
    return record


def test_json_formatter_produces_valid_json_with_message():
    formatter = JsonFormatter()
    record = _record("hello world")
    payload = json.loads(formatter.format(record))
    assert payload["message"] == "hello world"
    assert payload["level"] == "INFO"
    assert payload["logger"] == "test.logger"
    assert "timestamp" in payload


def test_json_formatter_includes_structured_extra_fields():
    formatter = JsonFormatter()
    record = _record(
        "request completed",
        request_id="req-123",
        method="GET",
        path="/v1/whoami",
        status_code=200,
        duration_ms=12.5,
    )
    payload = json.loads(formatter.format(record))
    assert payload["request_id"] == "req-123"
    assert payload["method"] == "GET"
    assert payload["path"] == "/v1/whoami"
    assert payload["status_code"] == 200
    assert payload["duration_ms"] == 12.5


def test_json_formatter_omits_unset_structured_fields():
    formatter = JsonFormatter()
    record = _record("plain message")
    payload = json.loads(formatter.format(record))
    for field in ("request_id", "job_id", "job_name", "method", "path", "status_code", "duration_ms"):
        assert field not in payload


def test_json_formatter_includes_exception_traceback():
    formatter = JsonFormatter()
    try:
        raise ValueError("boom")
    except ValueError:
        import sys

        record = logging.LogRecord(
            name="test.logger",
            level=logging.ERROR,
            pathname=__file__,
            lineno=1,
            msg="job failed",
            args=(),
            exc_info=sys.exc_info(),
        )
    payload = json.loads(formatter.format(record))
    assert "ValueError: boom" in payload["exception"]


def test_log_job_logs_completion_with_job_id_and_duration(monkeypatch, caplog):
    monkeypatch.setattr("rq.get_current_job", lambda: type("Job", (), {"id": "job-abc"})())

    @log_job
    def do_work(x: int) -> int:
        return x * 2

    with caplog.at_level(logging.INFO, logger="scan_worker.jobs"):
        result = do_work(21)

    assert result == 42
    record = next(r for r in caplog.records if r.message == "job completed")
    assert record.job_id == "job-abc"
    assert record.job_name == "do_work"
    assert record.duration_ms >= 0


def test_log_job_logs_failure_and_reraises(caplog):
    @log_job
    def failing_job() -> None:
        raise RuntimeError("kaboom")

    with caplog.at_level(logging.ERROR, logger="scan_worker.jobs"):
        with pytest.raises(RuntimeError, match="kaboom"):
            failing_job()

    record = next(r for r in caplog.records if r.message == "job failed")
    assert record.job_name == "failing_job"


def test_log_job_alerts_on_failure(monkeypatch):
    # A job function reaching log_job's except block means whatever it does
    # to handle expected failures (a dead token, a flaky external API)
    # didn't catch it - much more likely a real bug than routine noise, so
    # this is the one place worth a live alert rather than only a log line.
    from app_server import error_alerts

    calls = []
    monkeypatch.setattr(error_alerts, "send_error_alert", lambda *a, **k: calls.append((a, k)))

    @log_job
    def failing_job() -> None:
        raise RuntimeError("kaboom")

    with pytest.raises(RuntimeError):
        failing_job()

    assert len(calls) == 1
    args, kwargs = calls[0]
    assert args[0] == "failing_job"
    assert isinstance(args[1], RuntimeError)


def test_log_job_does_not_alert_on_success(monkeypatch):
    from app_server import error_alerts

    calls = []
    monkeypatch.setattr(error_alerts, "send_error_alert", lambda *a, **k: calls.append(1))

    @log_job
    def do_work() -> int:
        return 1

    do_work()

    assert calls == []
