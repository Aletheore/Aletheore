import os

from redis import Redis
from rq import Worker

from app_server.config import get_settings
from app_server.logging_config import configure_json_logging


if __name__ == "__main__":
    configure_json_logging()
    settings = get_settings()
    redis_conn = Redis.from_url(os.environ.get("REDIS_URL", settings.redis_url))
    # "health" is checked first (RQ's priority order), but this worker is
    # otherwise mostly idle between the ~3-minute sweep ticks, so it also
    # takes "email" - transactional sends are single fast HTTP calls, not
    # the multi-minute AI jobs "scans" carries, so this doesn't reintroduce
    # the contention problem "health" was split out to fix in the first
    # place.
    Worker(["health", "email"], connection=redis_conn).work()
