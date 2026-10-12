import ipaddress

import httpx
import pytest

from app_server import paddle_ip_allowlist
from app_server.paddle_ip_allowlist import (
    _fetch_paddle_networks as _real_fetch_paddle_networks,
)
from app_server.paddle_ip_allowlist import client_ip_from_forwarded_for, is_known_paddle_ip

# conftest.py's autouse _no_real_paddle_ip_fetch fixture replaces
# paddle_ip_allowlist._fetch_paddle_networks itself with a stub for every
# test in the suite (so a full-route webhook test never makes a real
# network call). That's exactly the function the two tests below exercise
# directly - calling it via the module attribute would silently run the
# fixture's stub instead of the real implementation. _real_fetch_paddle_
# networks is captured here, at import time, before any fixture runs, so
# it always points to the genuine function regardless of what the module
# attribute currently holds.


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

    result = asyncio.run(_real_fetch_paddle_networks())
    assert result is None


def test_fetch_paddle_networks_returns_none_for_an_empty_cidr_list(monkeypatch):
    # Code-review finding: a successful fetch that returns a legitimately
    # (or erroneously) empty ipv4_cidrs used to get cached as valid data,
    # not a failure - is_known_paddle_ip then returned False for every IP
    # (membership in an empty list) until the next successful non-empty
    # fetch, silently rejecting every real Paddle webhook for up to the
    # full TTL. Paddle's real range is never actually empty, so this is
    # treated the same as a fetch failure now.
    async def _fake_get(*args, **kwargs):
        request = httpx.Request("GET", "https://api.paddle.com/ips")
        return httpx.Response(200, json={"data": {"ipv4_cidrs": []}}, request=request)

    monkeypatch.setattr(httpx.AsyncClient, "get", _fake_get)

    import asyncio

    result = asyncio.run(_real_fetch_paddle_networks())
    assert result is None


def test_fetch_paddle_networks_returns_none_for_a_non_list_ipv4_cidrs(monkeypatch):
    # Code-review finding: a 200 response with ipv4_cidrs as null (or any
    # other non-list JSON value) assigned cleanly with no exception, so
    # "for cidr in cidrs" ran next and raised an uncaught TypeError - a
    # worse outcome (handler crash) than the empty-list case just above.
    async def _fake_get(*args, **kwargs):
        request = httpx.Request("GET", "https://api.paddle.com/ips")
        return httpx.Response(200, json={"data": {"ipv4_cidrs": None}}, request=request)

    monkeypatch.setattr(httpx.AsyncClient, "get", _fake_get)

    import asyncio

    result = asyncio.run(_real_fetch_paddle_networks())
    assert result is None


def test_fetch_paddle_networks_skips_one_malformed_cidr_keeps_the_rest(monkeypatch, caplog):
    # Code-review finding: ipaddress.ip_network's default strict=True
    # raised on one malformed CIDR anywhere in Paddle's response, and the
    # list comprehension this replaced let that exception discard every
    # OTHER valid CIDR in the same fetch too - indistinguishable from a
    # full outage.
    async def _fake_get(*args, **kwargs):
        request = httpx.Request("GET", "https://api.paddle.com/ips")
        return httpx.Response(
            200,
            json={"data": {"ipv4_cidrs": ["not-a-real-cidr", "203.0.113.0/24"]}},
            request=request,
        )

    monkeypatch.setattr(httpx.AsyncClient, "get", _fake_get)

    import asyncio

    with caplog.at_level("WARNING"):
        result = asyncio.run(_real_fetch_paddle_networks())

    assert result == [ipaddress.ip_network("203.0.113.0/24")]
    assert any("malformed" in r.message for r in caplog.records)


def test_fetch_paddle_networks_tolerates_a_cidr_with_host_bits_set(monkeypatch):
    # Code-review finding: ipaddress.ip_network("203.0.113.5/24") raises
    # under the default strict=True ("has host bits set"); strict=False
    # parses it as the intended 203.0.113.0/24 instead of discarding it
    # (and, before the previous fix, the whole fetch with it).
    async def _fake_get(*args, **kwargs):
        request = httpx.Request("GET", "https://api.paddle.com/ips")
        return httpx.Response(200, json={"data": {"ipv4_cidrs": ["203.0.113.5/24"]}}, request=request)

    monkeypatch.setattr(httpx.AsyncClient, "get", _fake_get)

    import asyncio

    result = asyncio.run(_real_fetch_paddle_networks())
    assert result == [ipaddress.ip_network("203.0.113.0/24")]


@pytest.mark.asyncio
async def test_stale_cache_stops_being_trusted_past_the_staleness_ceiling(monkeypatch):
    # Code-review finding: the stale-cache fallback had no staleness
    # ceiling - once any fetch had ever succeeded, a sustained later
    # outage reused that same old list forever. If Paddle rotates/adds a
    # sending IP during that window, a real webhook from the new IP fails
    # the membership check and gets hard-rejected anyway - the outcome the
    # fallback exists to avoid. Past the ceiling, this must return None
    # ("can't verify") instead, which the caller does not reject on.
    import time as time_module

    async def _fake_fetch():
        return None  # every refetch fails

    very_old = time_module.monotonic() - (paddle_ip_allowlist._CACHE_MAX_STALE_SECONDS + 1)
    monkeypatch.setattr(paddle_ip_allowlist, "_cache", (very_old, [ipaddress.ip_network("203.0.113.0/24")]))
    monkeypatch.setattr(paddle_ip_allowlist, "_fetch_paddle_networks", _fake_fetch)

    assert await is_known_paddle_ip("203.0.113.42") is None


@pytest.mark.asyncio
async def test_is_known_paddle_ip_true_for_an_ipv4_mapped_ipv6_literal(monkeypatch):
    # Code-review finding: IPv6Address in IPv4Network silently returns
    # False rather than comparing the embedded bits - a dual-stack socket
    # with IPV6_V6ONLY disabled can surface a real Paddle IPv4 peer in
    # this literal form.
    async def _fake_fetch():
        return [ipaddress.ip_network("203.0.113.0/24")]

    monkeypatch.setattr(paddle_ip_allowlist, "_cache", None)
    monkeypatch.setattr(paddle_ip_allowlist, "_fetch_paddle_networks", _fake_fetch)

    assert await is_known_paddle_ip("::ffff:203.0.113.42") is True


def test_client_ip_from_forwarded_for_uses_last_entry():
    # Caddy's reverse_proxy appends the real connecting peer as the LAST
    # entry - earlier entries could be attacker-supplied on the original
    # request before it ever reached Caddy.
    assert client_ip_from_forwarded_for("1.2.3.4, 5.6.7.8", "9.9.9.9") == "5.6.7.8"


def test_client_ip_from_forwarded_for_falls_back_when_header_absent():
    assert client_ip_from_forwarded_for(None, "9.9.9.9") == "9.9.9.9"


def test_client_ip_from_forwarded_for_falls_back_when_header_empty():
    assert client_ip_from_forwarded_for("", "9.9.9.9") == "9.9.9.9"


def test_client_ip_from_forwarded_for_falls_back_on_a_trailing_comma():
    # Code-review finding: a malformed/trailing-comma header from any hop
    # in the chain makes the extracted last segment itself empty, even
    # though the header string as a whole is non-empty - the `if not
    # forwarded_for` guard only caught the latter, so "" used to pass
    # straight through as the resolved client IP.
    assert client_ip_from_forwarded_for("1.2.3.4,", "9.9.9.9") == "9.9.9.9"
