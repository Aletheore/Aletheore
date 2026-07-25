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
    audit_signing_private_key: str
    paddle_webhook_secret: str
    paddle_client_token: str
    paddle_environment: str
    github_app_slug: str
    github_demo_readonly_token: str | None


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
        value = open(path).read().strip()
        if not value:
            raise RuntimeError("GITHUB_APP_PRIVATE_KEY_PATH points to an empty file")
        return value
    return _required_env("GITHUB_APP_PRIVATE_KEY")


def _paddle_environment() -> str:
    # Flash review (PR #32) correctly flagged hardcoded sandbox Paddle
    # credentials in frontend.py: a deploy that silently forgot to swap them
    # would take real payments in sandbox mode forever, or reject them
    # outright. Failing loudly here beats a live checkout that quietly never
    # charges anyone.
    value = os.environ.get("PADDLE_ENVIRONMENT", "sandbox").strip() or "sandbox"
    if value not in ("sandbox", "production"):
        raise RuntimeError(f"PADDLE_ENVIRONMENT must be 'sandbox' or 'production', got {value!r}")
    return value


def _paddle_client_token(environment: str) -> str:
    token = _required_env("PADDLE_CLIENT_TOKEN")
    if environment == "production" and token.startswith("test_"):
        raise RuntimeError(
            "PADDLE_ENVIRONMENT is 'production' but PADDLE_CLIENT_TOKEN looks like a sandbox "
            "token (starts with 'test_') - refusing to start with a config that would silently "
            "process live checkouts against the sandbox"
        )
    return token


def get_settings() -> Settings:
    paddle_environment = _paddle_environment()
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
        audit_signing_private_key=_required_env("AUDIT_SIGNING_PRIVATE_KEY"),
        paddle_webhook_secret=_required_env("PADDLE_WEBHOOK_SECRET"),
        paddle_client_token=_paddle_client_token(paddle_environment),
        paddle_environment=paddle_environment,
        github_app_slug=_required_env("GITHUB_APP_SLUG"),
        # Only used to raise the rate limit on the pre-clone repo-size check
        # for the public demo (60/hr unauthenticated vs 5000/hr with a
        # token) - a public_repo-scoped read-only PAT is enough, and the
        # demo works without one, just at a much lower shared ceiling.
        github_demo_readonly_token=os.environ.get("GITHUB_DEMO_READONLY_TOKEN", "").strip() or None,
    )
