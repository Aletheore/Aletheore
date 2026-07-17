import os

import pytest

from app_server.config import get_settings


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch):
    for key in (
        "DATABASE_URL",
        "GITHUB_APP_PRIVATE_KEY",
        "GITHUB_APP_PRIVATE_KEY_PATH",
        "GITHUB_CLIENT_ID",
        "GITHUB_CLIENT_SECRET",
        "SESSION_SECRET",
        "PUBLIC_BASE_URL",
    ):
        monkeypatch.delenv(key, raising=False)
    monkeypatch.setenv("DATABASE_URL", "postgresql://u:p@localhost/db")


def test_reads_private_key_from_path_when_set(tmp_path, monkeypatch):
    key_file = tmp_path / "key.pem"
    key_file.write_text("-----BEGIN RSA PRIVATE KEY-----\nabc\n-----END RSA PRIVATE KEY-----\n")
    monkeypatch.setenv("GITHUB_APP_PRIVATE_KEY_PATH", str(key_file))
    settings = get_settings()
    assert settings.github_app_private_key == key_file.read_text()


def test_falls_back_to_raw_env_var_when_no_path_set(monkeypatch):
    monkeypatch.setenv("GITHUB_APP_PRIVATE_KEY", "raw-key-value")
    settings = get_settings()
    assert settings.github_app_private_key == "raw-key-value"


def test_path_takes_precedence_over_raw_env_var(tmp_path, monkeypatch):
    key_file = tmp_path / "key.pem"
    key_file.write_text("from-file")
    monkeypatch.setenv("GITHUB_APP_PRIVATE_KEY_PATH", str(key_file))
    monkeypatch.setenv("GITHUB_APP_PRIVATE_KEY", "from-env-var")
    settings = get_settings()
    assert settings.github_app_private_key == "from-file"


def test_reads_paid_tier_settings(monkeypatch):
    monkeypatch.setenv("GITHUB_CLIENT_ID", "client-id")
    monkeypatch.setenv("GITHUB_CLIENT_SECRET", "client-secret")
    monkeypatch.setenv("SESSION_SECRET", "session-secret")
    monkeypatch.setenv("PUBLIC_BASE_URL", "https://example.com")

    settings = get_settings()

    assert settings.github_client_id == "client-id"
    assert settings.github_client_secret == "client-secret"
    assert settings.session_secret == "session-secret"
    assert settings.public_base_url == "https://example.com"
