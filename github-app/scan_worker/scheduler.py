import time

from redis import Redis
from rq import Queue

from app_server.config import get_settings

HEALTH_SWEEP_INTERVAL_SECONDS = 180


def run_forever(
    interval_seconds: int = HEALTH_SWEEP_INTERVAL_SECONDS,
    max_iterations: int | None = None,
) -> None:
    settings = get_settings()
    queue = Queue("scans", connection=Redis.from_url(settings.redis_url))
    iterations = 0
    while max_iterations is None or iterations < max_iterations:
        queue.enqueue("scan_worker.jobs.run_health_check_sweep_job")
        iterations += 1
        if max_iterations is not None and iterations >= max_iterations:
            break
        time.sleep(interval_seconds)


if __name__ == "__main__":
    run_forever()
