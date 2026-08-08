import os

from redis import Redis
from rq import Worker

from app_server.config import get_settings
from app_server.logging_config import configure_json_logging


if __name__ == "__main__":
    configure_json_logging()
    settings = get_settings()
    redis_conn = Redis.from_url(os.environ.get("REDIS_URL", settings.redis_url))
    Worker(["health"], connection=redis_conn).work()
