import functools
import json
import logging
import sys
import time

_STRUCTURED_FIELDS = (
    "request_id",
    "job_id",
    "job_name",
    "method",
    "path",
    "status_code",
    "duration_ms",
    "deleted_count",
)


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        payload = {
            "timestamp": self.formatTime(record, "%Y-%m-%dT%H:%M:%S%z"),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
        }
        for key in _STRUCTURED_FIELDS:
            value = getattr(record, key, None)
            if value is not None:
                payload[key] = value
        if record.exc_info:
            payload["exception"] = self.formatException(record.exc_info)
        return json.dumps(payload)


def configure_json_logging(level: int = logging.INFO) -> None:
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(JsonFormatter())
    root = logging.getLogger()
    root.handlers = [handler]
    root.setLevel(level)


_job_logger = logging.getLogger("scan_worker.jobs")


def log_job(func):
    """Log a background job's start/end with its RQ job ID and duration.

    Kept independent of RQ's own logging (rq.worker sets up its own
    handler and format) so job-level entries are structured JSON
    regardless of what RQ itself does with its logger.
    """

    @functools.wraps(func)
    def wrapper(*args, **kwargs):
        from rq import get_current_job

        job = get_current_job()
        job_id = job.id if job is not None else None
        start = time.monotonic()
        try:
            result = func(*args, **kwargs)
        except Exception:
            duration_ms = round((time.monotonic() - start) * 1000, 2)
            _job_logger.exception(
                "job failed",
                extra={"job_id": job_id, "job_name": func.__name__, "duration_ms": duration_ms},
            )
            raise
        duration_ms = round((time.monotonic() - start) * 1000, 2)
        _job_logger.info(
            "job completed",
            extra={"job_id": job_id, "job_name": func.__name__, "duration_ms": duration_ms},
        )
        return result

    return wrapper
