import os
from dataclasses import dataclass


@dataclass(frozen=True)
class Settings:
    database_url: str
    redis_url: str
    github_app_id: str
    github_app_private_key: str
    github_webhook_secret: str


def _load_private_key() -> str:
    # A PEM private key contains real newlines, which plain env-file values
    # (docker run/compose --env-file) reject outright - confirmed empirically
    # against the actual GitHub App key, not assumed. GITHUB_APP_PRIVATE_KEY_PATH
    # (a mounted file) is the primary path; GITHUB_APP_PRIVATE_KEY stays as a
    # fallback for environments that inject the value some other way (e.g. a
    # secrets manager that sets real env vars directly, bypassing env-file
    # parsing entirely).
    path = os.environ.get("GITHUB_APP_PRIVATE_KEY_PATH", "")
    if path:
        return open(path).read()
    return os.environ.get("GITHUB_APP_PRIVATE_KEY", "")


def get_settings() -> Settings:
    return Settings(
        database_url=os.environ["DATABASE_URL"],
        redis_url=os.environ.get("REDIS_URL", "redis://localhost:6379/0"),
        github_app_id=os.environ.get("GITHUB_APP_ID", ""),
        github_app_private_key=_load_private_key(),
        github_webhook_secret=os.environ.get("GITHUB_WEBHOOK_SECRET", ""),
    )
