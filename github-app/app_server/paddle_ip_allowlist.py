import ipaddress
import logging
import time

import httpx

logger = logging.getLogger(__name__)

_PADDLE_IPS_URL = "https://api.paddle.com/ips"
_CACHE_TTL_SECONDS = 3600.0

# (fetched_at, networks) - module-level so the list is fetched at most once
# per TTL across the whole process, not once per webhook request.
_cache: tuple[float, list[ipaddress.IPv4Network]] | None = None


async def _fetch_paddle_networks() -> list[ipaddress.IPv4Network] | None:
    try:
        async with httpx.AsyncClient() as client:
            response = await client.get(_PADDLE_IPS_URL, timeout=5.0)
        response.raise_for_status()
        cidrs = response.json()["data"]["ipv4_cidrs"]
        return [ipaddress.ip_network(cidr) for cidr in cidrs]
    except (httpx.HTTPError, KeyError, ValueError, TypeError) as exc:
        logger.warning("failed to fetch Paddle's published IP list: %s", exc)
        return None


async def _cached_paddle_networks() -> list[ipaddress.IPv4Network] | None:
    global _cache
    now = time.monotonic()
    if _cache is not None and now - _cache[0] < _CACHE_TTL_SECONDS:
        return _cache[1]
    networks = await _fetch_paddle_networks()
    if networks is None:
        # A transient fetch failure shouldn't turn into "reject every real
        # webhook until the next successful fetch" - reuse the last known-good
        # list (if any) rather than treating "couldn't reach Paddle's IP
        # endpoint" as "no IPs are Paddle's".
        return _cache[1] if _cache is not None else None
    _cache = (now, networks)
    return networks


async def is_known_paddle_ip(ip: str) -> bool | None:
    """True/False once checked against Paddle's published IP range, or None
    if the range itself couldn't be fetched (fresh or cached) - callers
    should treat None as "can't verify" rather than "reject", since webhook
    signature verification remains the real security boundary and this is
    defense-in-depth on top of it, not a replacement for it."""
    networks = await _cached_paddle_networks()
    if networks is None:
        return None
    try:
        address = ipaddress.ip_address(ip)
    except ValueError:
        return False
    return any(address in network for network in networks)


def client_ip_from_forwarded_for(forwarded_for: str | None, fallback: str) -> str:
    if not forwarded_for:
        return fallback
    # Caddy's reverse_proxy appends the real connecting peer's IP as the LAST
    # entry in X-Forwarded-For - any earlier entries could be attacker-supplied
    # on the original request before it ever reached Caddy.
    return forwarded_for.split(",")[-1].strip()
