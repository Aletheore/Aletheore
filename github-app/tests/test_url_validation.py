import socket

import pytest

from app_server.url_validation import UnsafeURLError, validate_external_https_url


def _fake_addrinfo(ip: str):
    family = socket.AF_INET6 if ":" in ip else socket.AF_INET
    return [(family, socket.SOCK_STREAM, 6, "", (ip, 443))]


def test_rejects_non_https_scheme():
    with pytest.raises(UnsafeURLError, match="https"):
        validate_external_https_url("http://api.example.com")


def test_rejects_url_without_hostname():
    with pytest.raises(UnsafeURLError, match="hostname"):
        validate_external_https_url("https:///path")


def test_rejects_when_dns_resolution_fails(monkeypatch):
    def _raise(*args, **kwargs):
        raise socket.gaierror("no such host")

    monkeypatch.setattr("app_server.url_validation.socket.getaddrinfo", _raise)
    with pytest.raises(UnsafeURLError, match="could not resolve"):
        validate_external_https_url("https://no-such-host.invalid")


@pytest.mark.parametrize(
    "ip",
    [
        "10.0.0.5",
        "172.16.0.5",
        "192.168.1.5",
        "127.0.0.1",
        "169.254.169.254",
        "0.0.0.0",
        "::1",
        "fe80::1",
    ],
)
def test_rejects_internal_addresses(monkeypatch, ip):
    monkeypatch.setattr(
        "app_server.url_validation.socket.getaddrinfo",
        lambda *a, **k: _fake_addrinfo(ip),
    )
    with pytest.raises(UnsafeURLError, match="disallowed"):
        validate_external_https_url("https://internal.example.com")


def test_allows_public_address(monkeypatch):
    monkeypatch.setattr(
        "app_server.url_validation.socket.getaddrinfo",
        lambda *a, **k: _fake_addrinfo("93.184.216.34"),
    )
    assert validate_external_https_url("https://api.example.com") == "https://api.example.com"
