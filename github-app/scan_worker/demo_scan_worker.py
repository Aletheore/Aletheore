import os

from redis import Redis
from rq import Worker

from app_server.logging_config import configure_json_logging

# Deliberately does not import app_server.config.get_settings() - that
# pulls in every secret the app has (DB credentials, GitHub App key, LLM
# keys) via one required-env dataclass construction. This worker only
# ever needs REDIS_URL, so it reads that directly and nothing else.
if __name__ == "__main__":
    configure_json_logging()
    redis_conn = Redis.from_url(os.environ.get("REDIS_URL", "redis://localhost:6379/0"))
    Worker(["demo_scan"], connection=redis_conn).work()
