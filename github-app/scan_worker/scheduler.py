import time

from redis import Redis
from rq import Queue

from app_server.config import get_settings
from app_server.logging_config import configure_json_logging

HEALTH_SWEEP_INTERVAL_SECONDS = 180
# The sweep pings every endpoint for every repo for every monitored
# installation, serially, each bounded to 5s by run_healthcheck's own
# per-request timeout. 600s is a deliberate ceiling well above normal
# runtime that still guarantees the job can't run forever and pile up
# against the next scheduler tick indefinitely.
HEALTH_SWEEP_JOB_TIMEOUT_SECONDS = 600
# A single indexed DELETE against a small table - generous but bounded.
SESSION_CLEANUP_JOB_TIMEOUT_SECONDS = 60


def run_forever(
    interval_seconds: int = HEALTH_SWEEP_INTERVAL_SECONDS,
    max_iterations: int | None = None,
) -> None:
    settings = get_settings()
    queue = Queue("scans", connection=Redis.from_url(settings.redis_url))
    iterations = 0
    while max_iterations is None or iterations < max_iterations:
        queue.enqueue(
            "scan_worker.jobs.run_health_check_sweep_job",
            job_timeout=HEALTH_SWEEP_JOB_TIMEOUT_SECONDS,
        )
        queue.enqueue(
            "scan_worker.jobs.run_session_cleanup_job",
            job_timeout=SESSION_CLEANUP_JOB_TIMEOUT_SECONDS,
        )
        iterations += 1
        if max_iterations is not None and iterations >= max_iterations:
            break
        time.sleep(interval_seconds)


if __name__ == "__main__":
    configure_json_logging()
    run_forever()
