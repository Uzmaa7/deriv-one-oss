"""SSRF guards for server-side HTTP fetches.

Validates scheme, hostname, and resolved addresses before a request is sent,
and re-validates each redirect target via httpx request hooks.
"""

from __future__ import annotations

import ipaddress
import socket
from typing import Optional
from urllib.parse import urlparse


_BLOCKED_HOSTNAMES = frozenset(
    {
        "localhost",
        "localhost.localdomain",
        "metadata.google.internal",
        "metadata",
    }
)


def _hostname_blocked(hostname: str) -> Optional[str]:
    host = (hostname or "").strip().lower().rstrip(".")
    if not host:
        return "Invalid URL — no hostname found."
    if host in _BLOCKED_HOSTNAMES:
        return "Fetching private or internal URLs is not permitted."
    if host.endswith(".localhost") or host.endswith(".local"):
        return "Fetching private or internal URLs is not permitted."
    return None


def _ip_blocked(addr: ipaddress.IPv4Address | ipaddress.IPv6Address) -> bool:
    return bool(
        addr.is_private
        or addr.is_loopback
        or addr.is_link_local
        or addr.is_reserved
        or addr.is_multicast
        or addr.is_unspecified
    )


def _resolve_host_ips(hostname: str) -> list[str]:
    try:
        infos = socket.getaddrinfo(hostname, None, type=socket.SOCK_STREAM)
    except OSError as exc:
        raise ValueError(f"Could not resolve hostname '{hostname}'.") from exc
    ips: list[str] = []
    for info in infos:
        sockaddr = info[4]
        if sockaddr:
            ips.append(sockaddr[0])
    if not ips:
        raise ValueError(f"Could not resolve hostname '{hostname}'.")
    return ips


def validate_fetch_url(url: str) -> tuple[bool, str]:
    """Return (ok, error_message). Empty error_message when ok is True."""
    parsed = urlparse(url)
    if parsed.scheme not in ("http", "https"):
        return False, "Only http:// and https:// URLs are supported."

    hostname = parsed.hostname
    blocked = _hostname_blocked(hostname or "")
    if blocked:
        return False, blocked

    # Literal IP in URL
    if hostname:
        try:
            literal = ipaddress.ip_address(hostname)
            if _ip_blocked(literal):
                return False, "Fetching private or internal URLs is not permitted."
            return True, ""
        except ValueError:
            pass

    try:
        for ip_str in _resolve_host_ips(hostname or ""):
            addr = ipaddress.ip_address(ip_str)
            if _ip_blocked(addr):
                return False, "Fetching private or internal URLs is not permitted."
    except ValueError as exc:
        return False, str(exc)

    return True, ""


def httpx_request_hook(request) -> None:
    """httpx event hook — reject disallowed redirect / request targets."""
    ok, err = validate_fetch_url(str(request.url))
    if not ok:
        import httpx

        raise httpx.RequestError(err)
