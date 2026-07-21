import os
from dataclasses import dataclass


@dataclass(frozen=True)
class Settings:
    database_url: str
    redis_url: str
    github_app_id: str
    github_app_private_key: str
    github_webhook_secret: str
    github_client_id: str
    github_client_secret: str
    session_secret: str
    public_base_url: str
    internal_metrics_token: str | None


def _required_env(name: str) -> str:
    value = os.environ.get(name, "").strip()
    if not value:
        raise RuntimeError(f"{name} is required")
    return value


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
        github_webhook_secret=_required_env("GITHUB_WEBHOOK_SECRET"),
        github_client_id=os.environ.get("GITHUB_CLIENT_ID", ""),
        github_client_secret=_required_env("GITHUB_CLIENT_SECRET"),
        session_secret=_required_env("SESSION_SECRET"),
        public_base_url=os.environ.get("PUBLIC_BASE_URL", "https://aletheore.com"),
        internal_metrics_token=os.environ.get("INTERNAL_METRICS_TOKEN", "").strip() or None,
    )
