from urllib.parse import quote

from app_server.config import get_settings


def github_app_install_url(next_path: str) -> str:
    settings = get_settings()
    return f"https://github.com/apps/{settings.github_app_slug}/installations/new?state={quote(next_path)}"
