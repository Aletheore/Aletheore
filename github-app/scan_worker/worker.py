import os

from redis import Redis
from rq import Worker

from app_server.config import get_settings


if __name__ == "__main__":
    settings = get_settings()
    redis_conn = Redis.from_url(os.environ.get("REDIS_URL", settings.redis_url))
    Worker(["scans"], connection=redis_conn).work()
