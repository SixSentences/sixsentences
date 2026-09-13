"""Egress guard against SSRF.

Any URL we fetch on a tenant's behalf — a webhook target, an open-access
location taken from third-party (OpenAlex/corpus) metadata, a scraped
``citation_pdf_url`` — must point at a *public* host. We reject non-http(s)
schemes and any address that is private, loopback, link-local (this covers the
``169.254.169.254`` cloud-metadata endpoint), CGNAT, reserved, multicast or
unspecified. Host *names* are resolved and **every** returned address is
checked, so a name with an A-record inside the private range is refused too.

Residual (documented, not closed here): a deliberate DNS-rebinding TOCTOU race —
the name could resolve to a public IP at check time and a private one at connect
time. Callers that fetch a body (acquisition) validate every redirect hop and
cap their exposure; pinning the checked IP for the actual socket is future work.
"""

import ipaddress
import socket
from collections.abc import Callable
from urllib.parse import urlparse

# Hostnames with no literal IP but a well-known internal meaning.
_BLOCKED_HOSTNAMES = frozenset({"localhost", "metadata.google.internal"})
# Carrier-grade NAT (RFC 6598) is not flagged by ``is_private`` on all versions.
_CGNAT_V4 = ipaddress.ip_network("100.64.0.0/10")

Resolver = Callable[[str], list[str]]

_IPAddress = ipaddress.IPv4Address | ipaddress.IPv6Address


def _system_resolver(host: str) -> list[str]:
    return [str(info[4][0]) for info in socket.getaddrinfo(host, None, proto=socket.IPPROTO_TCP)]


def _ip_is_public(ip: _IPAddress) -> bool:
    if isinstance(ip, ipaddress.IPv6Address) and ip.ipv4_mapped is not None:
        ip = ip.ipv4_mapped  # unwrap ::ffff:10.0.0.1 so the v4 rules apply
    if (
        ip.is_private
        or ip.is_loopback
        or ip.is_link_local
        or ip.is_reserved
        or ip.is_multicast
        or ip.is_unspecified
    ):
        return False
    return not (isinstance(ip, ipaddress.IPv4Address) and ip in _CGNAT_V4)


def is_public_http_url(url: str, *, resolve: bool = True, resolver: Resolver | None = None) -> bool:
    """True only if `url` is http(s) to a public host.

    With ``resolve=True`` (default) the hostname is resolved and every address it
    maps to must be public — an unresolvable host is refused. ``resolve=False``
    does the network-free structural checks only (scheme + literal-IP class),
    which already blocks a literal ``http://169.254.169.254/…`` SSRF probe.
    """
    try:
        parsed = urlparse(url)
    except ValueError:
        return False
    if parsed.scheme not in ("http", "https"):
        return False
    host = (parsed.hostname or "").lower()
    if not host or host in _BLOCKED_HOSTNAMES:
        return False
    try:
        return _ip_is_public(ipaddress.ip_address(host))  # a literal IP
    except ValueError:
        pass  # a hostname — fall through to resolution
    if not resolve:
        return True
    try:
        addresses = (resolver or _system_resolver)(host)
    except OSError:
        return False  # unresolvable -> refuse rather than connect blindly
    if not addresses:
        return False
    for address in addresses:
        try:
            ip = ipaddress.ip_address(address)
        except ValueError:
            return False
        if not _ip_is_public(ip):
            return False
    return True
