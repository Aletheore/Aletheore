import os
from dataclasses import dataclass


@dataclass(frozen=True)
class Settings:
    database_url: str
    redis_url: str
    github_app_id: str
    github_app_private_key: str
    github_webhook_secret: str


def get_settings() -> Settings:
    return Settings(
        database_url=os.environ["DATABASE_URL"],
        redis_url=os.environ.get("REDIS_URL", "redis://localhost:6379/0"),
        github_app_id=os.environ.get("GITHUB_APP_ID", ""),
        github_app_private_key=os.environ.get("GITHUB_APP_PRIVATE_KEY", ""),
        github_webhook_secret=os.environ.get("GITHUB_WEBHOOK_SECRET", ""),
    )
