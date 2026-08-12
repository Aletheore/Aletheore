import ipaddress
import socket
from urllib.parse import urlparse


class UnsafeURLError(ValueError):
    pass


_SHARED_ADDRESS_SPACE = ipaddress.ip_network("100.64.0.0/10")


def _is_disallowed_ip(ip: ipaddress.IPv4Address | ipaddress.IPv6Address) -> bool:
    mapped_ipv4 = ip.ipv4_mapped if isinstance(ip, ipaddress.IPv6Address) else None
    return (
        ip.is_private
        or ip.is_loopback
        or ip.is_link_local
        or ip.is_reserved
        or ip.is_multicast
        or ip.is_unspecified
        or ip in _SHARED_ADDRESS_SPACE
        or (mapped_ipv4 is not None and mapped_ipv4 in _SHARED_ADDRESS_SPACE)
    )


def _validate_and_resolve(raw: str) -> list[str]:
    parsed = urlparse(raw)
    if parsed.scheme != "https":
        raise UnsafeURLError("URL must use https")

    hostname = parsed.hostname
    if not hostname:
        raise UnsafeURLError("URL must include a hostname")

    try:
        addresses = socket.getaddrinfo(hostname, None)
    except socket.gaierror as exc:
        raise UnsafeURLError(f"could not resolve host '{hostname}'") from exc

    resolved_ips = []
    for entry in addresses:
        ip_str = entry[4][0]
        ip = ipaddress.ip_address(ip_str)
        if _is_disallowed_ip(ip):
            raise UnsafeURLError(f"'{hostname}' resolves to a disallowed address")
        resolved_ips.append(ip_str)
    return resolved_ips


def validate_external_https_url(raw: str) -> str:
    _validate_and_resolve(raw)
    return raw


def validate_and_pin_https_url(raw: str) -> tuple[str, str]:
    """Same validation as validate_external_https_url, but also returns one
    of the resolved-safe IPs the caller can pin its actual connection to.

    validate_external_https_url alone only narrows the DNS-rebinding window
    (a hostname that resolves safely here can still resolve somewhere
    unsafe by the time the real request's own DNS lookup runs) rather than
    closing it - resolving once and reusing that exact address for the
    connection (see aletheore.healthcheck.run_healthcheck's pinned_ip)
    closes it to zero, since there is no second, independent resolution
    left to race.
    """
    resolved_ips = _validate_and_resolve(raw)
    return raw, resolved_ips[0]
