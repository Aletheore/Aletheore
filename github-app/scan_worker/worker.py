import os

from redis import Redis
from rq import Worker

from app_server.config import get_settings
from app_server.heartbeat import start_heartbeat_thread
from app_server.logging_config import configure_json_logging
from app_server.sentry_config import init_sentry


_QUEUES_ENV = "SCAN_WORKER_QUEUES"
_DEFAULT_QUEUES = ["scans", "builds"]


def queue_names_from_env() -> list[str]:
    """The queues this worker serves, in priority order (RQ takes jobs from the first queue that has
    one, so a free worker always prefers a waiting review in "scans" over a waiting build).

    One replica can be restricted to "scans" so a pair of 30-minute AIRview/Docs builds can never occupy
    every worker and starve PR reviews (2026-10-30). A blank setting falls back to the default rather than
    leaving a worker that listens to nothing.
    """
    names = [name.strip() for name in os.environ.get(_QUEUES_ENV, "").split(",") if name.strip()]
    return names or list(_DEFAULT_QUEUES)


if __name__ == "__main__":
    configure_json_logging()
    init_sentry("scan_worker")
    settings = get_settings()
    redis_conn = Redis.from_url(os.environ.get("REDIS_URL", settings.redis_url))
    # See health_worker.py's comment - background thread, proves the
    # process hasn't fully deadlocked, not that RQ is actively dequeuing.
    start_heartbeat_thread()
    from scan_worker.jobs import purge_job_scratch_dir

    purge_job_scratch_dir()
    Worker(queue_names_from_env(), connection=redis_conn).work()
