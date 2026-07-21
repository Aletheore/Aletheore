import ipaddress
import socket
from urllib.parse import urlparse


class UnsafeURLError(ValueError):
    pass


def validate_external_https_url(raw: str) -> str:
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

    for entry in addresses:
        ip = ipaddress.ip_address(entry[4][0])
        if (
            ip.is_private
            or ip.is_loopback
            or ip.is_link_local
            or ip.is_reserved
            or ip.is_multicast
            or ip.is_unspecified
        ):
            raise UnsafeURLError(f"'{hostname}' resolves to a disallowed address")

    return raw
