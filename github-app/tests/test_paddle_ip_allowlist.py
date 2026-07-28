import ipaddress

import httpx
import pytest

from app_server import paddle_ip_allowlist
from app_server.paddle_ip_allowlist import client_ip_from_forwarded_for, is_known_paddle_ip


@pytest.mark.asyncio
async def test_is_known_paddle_ip_true_when_in_range(monkeypatch):
    async def _fake_fetch():
        return [ipaddress.ip_network("203.0.113.0/24")]

    monkeypatch.setattr(paddle_ip_allowlist, "_cache", None)
    monkeypatch.setattr(paddle_ip_allowlist, "_fetch_paddle_networks", _fake_fetch)

    assert await is_known_paddle_ip("203.0.113.42") is True


@pytest.mark.asyncio
async def test_is_known_paddle_ip_false_when_outside_range(monkeypatch):
    async def _fake_fetch():
        return [ipaddress.ip_network("203.0.113.0/24")]

    monkeypatch.setattr(paddle_ip_allowlist, "_cache", None)
    monkeypatch.setattr(paddle_ip_allowlist, "_fetch_paddle_networks", _fake_fetch)

    assert await is_known_paddle_ip("198.51.100.1") is False


@pytest.mark.asyncio
async def test_is_known_paddle_ip_none_when_fetch_fails(monkeypatch):
    async def _fake_fetch():
        return None

    monkeypatch.setattr(paddle_ip_allowlist, "_cache", None)
    monkeypatch.setattr(paddle_ip_allowlist, "_fetch_paddle_networks", _fake_fetch)

    assert await is_known_paddle_ip("203.0.113.42") is None


@pytest.mark.asyncio
async def test_is_known_paddle_ip_false_for_unparseable_ip(monkeypatch):
    async def _fake_fetch():
        return [ipaddress.ip_network("203.0.113.0/24")]

    monkeypatch.setattr(paddle_ip_allowlist, "_cache", None)
    monkeypatch.setattr(paddle_ip_allowlist, "_fetch_paddle_networks", _fake_fetch)

    assert await is_known_paddle_ip("not-an-ip") is False


@pytest.mark.asyncio
async def test_stale_cache_reused_when_refetch_fails(monkeypatch):
    # A transient outage reaching Paddle's own /ips endpoint shouldn't turn
    # into "reject every real webhook until the next successful fetch" -
    # the last known-good list should keep being used.
    call_count = 0

    async def _fake_fetch():
        nonlocal call_count
        call_count += 1
        if call_count == 1:
            return [ipaddress.ip_network("203.0.113.0/24")]
        return None

    monkeypatch.setattr(paddle_ip_allowlist, "_cache", None)
    monkeypatch.setattr(paddle_ip_allowlist, "_fetch_paddle_networks", _fake_fetch)
    monkeypatch.setattr(paddle_ip_allowlist, "_CACHE_TTL_SECONDS", -1.0)

    assert await is_known_paddle_ip("203.0.113.42") is True
    # TTL is negative, so this second call forces a re-fetch, which fails -
    # the stale cached network list from the first call should still be used.
    assert await is_known_paddle_ip("203.0.113.42") is True
    assert call_count == 2


def test_fetch_paddle_networks_handles_http_error(monkeypatch):
    async def _raise(*args, **kwargs):
        raise httpx.ConnectError("no network")

    monkeypatch.setattr(httpx.AsyncClient, "get", _raise)

    import asyncio

    result = asyncio.run(paddle_ip_allowlist._fetch_paddle_networks())
    assert result is None


def test_client_ip_from_forwarded_for_uses_last_entry():
    # Caddy's reverse_proxy appends the real connecting peer as the LAST
    # entry - earlier entries could be attacker-supplied on the original
    # request before it ever reached Caddy.
    assert client_ip_from_forwarded_for("1.2.3.4, 5.6.7.8", "9.9.9.9") == "5.6.7.8"


def test_client_ip_from_forwarded_for_falls_back_when_header_absent():
    assert client_ip_from_forwarded_for(None, "9.9.9.9") == "9.9.9.9"


def test_client_ip_from_forwarded_for_falls_back_when_header_empty():
    assert client_ip_from_forwarded_for("", "9.9.9.9") == "9.9.9.9"
