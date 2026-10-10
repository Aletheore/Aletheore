import ipaddress
import logging
import time

import httpx

logger = logging.getLogger(__name__)

_PADDLE_IPS_URL = "https://api.paddle.com/ips"
_CACHE_TTL_SECONDS = 3600.0

# A sustained outage reaching Paddle's /ips endpoint shouldn't turn into
# rejecting every real webhook (see _cached_paddle_networks' fallback
# below), but trusting arbitrarily old data forever isn't actually safer:
# if Paddle rotates or adds a sending IP while the cache is stuck this
# stale, a real webhook from the new IP fails the membership check below
# and gets hard-rejected anyway (webhooks/paddle.py's `is False` check) -
# the exact outcome the stale-cache fallback exists to avoid. Past this
# ceiling, the stale list is no longer trusted and this returns None
# ("can't verify") instead, which the caller does NOT reject on - safer
# than silently rejecting on data that's had ample time to go stale.
_CACHE_MAX_STALE_SECONDS = 7 * 24 * 3600.0

# (fetched_at, networks) - module-level so the list is fetched at most once
# per TTL across the whole process, not once per webhook request.
_cache: tuple[float, list[ipaddress.IPv4Network]] | None = None


async def _fetch_paddle_networks() -> list[ipaddress.IPv4Network] | None:
    try:
        async with httpx.AsyncClient() as client:
            response = await client.get(_PADDLE_IPS_URL, timeout=5.0)
        response.raise_for_status()
        cidrs = response.json()["data"]["ipv4_cidrs"]
    except (httpx.HTTPError, KeyError, ValueError, TypeError) as exc:
        logger.warning("failed to fetch Paddle's published IP list: %s", exc)
        return None

    networks = []
    for cidr in cidrs:
        try:
            # strict=False: Paddle publishing one non-canonical CIDR (host
            # bits set, e.g. "1.2.3.4/24") must not discard the whole
            # fetch - ipaddress.ip_network defaults to strict=True, which
            # raises ValueError on exactly that, and the list comprehension
            # this replaced let one bad entry anywhere in the response
            # throw away every other valid CIDR in it too.
            networks.append(ipaddress.ip_network(cidr, strict=False))
        except (ValueError, TypeError):
            logger.warning("skipping malformed CIDR in Paddle's published IP list: %r", cidr)

    if not networks:
        # Real risk, not hypothetical: an empty-but-successfully-parsed
        # list used to get cached as valid data (not a failure), so
        # is_known_paddle_ip's membership check was False for every IP
        # until the next successful non-empty fetch, up to the full TTL -
        # silently rejecting every real Paddle webhook despite a valid
        # signature. Paddle's real IP range is never actually empty, so
        # an empty result here is itself a signal something's wrong with
        # the response, not legitimate data - treat it the same as a
        # fetch failure (falls through to the stale-cache reuse below).
        logger.warning("Paddle's published IP list came back with no usable CIDRs")
        return None
    return networks


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
        # endpoint" as "no IPs are Paddle's". Past _CACHE_MAX_STALE_SECONDS,
        # stop trusting it (see that constant's own docstring).
        if _cache is not None and now - _cache[0] < _CACHE_MAX_STALE_SECONDS:
            return _cache[1]
        return None
    _cache = (now, networks)
    return networks


def _unwrap_ipv4_mapped(address: ipaddress.IPv4Address | ipaddress.IPv6Address):
    """An IPv4-mapped IPv6 literal (e.g. "::ffff:91.228.74.14") represents
    a real IPv4 address, but `IPv6Address in IPv4Network` silently returns
    False rather than comparing the embedded bits - a known behavior of
    dual-stack sockets when IPV6_V6ONLY is disabled can surface a peer
    address in exactly this form. Unwrap it before the membership check so
    a real Paddle IPv4 address isn't missed just because of how the
    underlying socket reported it."""
    if isinstance(address, ipaddress.IPv6Address) and address.ipv4_mapped is not None:
        return address.ipv4_mapped
    return address


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
        address = _unwrap_ipv4_mapped(ipaddress.ip_address(ip))
    except ValueError:
        return False
    return any(address in network for network in networks)


def client_ip_from_forwarded_for(forwarded_for: str | None, fallback: str) -> str:
    if not forwarded_for:
        return fallback
    # Caddy's reverse_proxy appends the real connecting peer's IP as the LAST
    # entry in X-Forwarded-For - any earlier entries could be attacker-supplied
    # on the original request before it ever reached Caddy.
    last_hop = forwarded_for.split(",")[-1].strip()
    # A malformed/trailing-comma header (e.g. "1.2.3.4,") makes the last
    # segment itself empty even though the full header string isn't - the
    # `if not forwarded_for` guard above only catches the latter. Returning
    # "" here used to pass straight through: is_known_paddle_ip("") raises
    # inside ipaddress.ip_address, returns False (not None), and a real
    # signature-valid webhook delivered through a proxy chain that
    # produces this got wrongly rejected as "not a Paddle IP".
    return last_hop or fallback
