from unittest.mock import patch

from app_server.github_install import github_app_install_url


def test_install_url_includes_slug_and_state():
    with patch("app_server.github_install.get_settings") as mock_settings:
        mock_settings.return_value.github_app_slug = "aletheore"
        url = github_app_install_url("/subscribe/claim")
    assert url.startswith("https://github.com/apps/aletheore/installations/new")
    assert "state=" in url
