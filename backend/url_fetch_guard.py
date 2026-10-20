"""SSRF guards for server-side HTTP fetches.

Validates scheme, hostname, and resolved addresses before a request is sent,
pins DNS resolution for the actual TCP connection, and re-validates redirect
targets.
"""

from __future__ import annotations

import contextlib
import ipaddress
import socket
from typing import Iterator, Optional
from urllib.parse import urljoin, urlparse


_CGNAT_NETWORK = ipaddress.ip_network("100.64.0.0/10")

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
    if addr in _CGNAT_NETWORK:
        return True
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


def pick_public_ip(hostname: str) -> str:
    for ip_str in _resolve_host_ips(hostname):
        if not _ip_blocked(ipaddress.ip_address(ip_str)):
            return ip_str
    raise ValueError("Fetching private or internal URLs is not permitted.")


def validate_fetch_url(url: str) -> tuple[bool, str]:
    """Return (ok, error_message). Empty error_message when ok is True."""
    parsed = urlparse(url)
    if parsed.scheme not in ("http", "https"):
        return False, "Only http:// and https:// URLs are supported."

    hostname = parsed.hostname
    blocked = _hostname_blocked(hostname or "")
    if blocked:
        return False, blocked

    if hostname:
        try:
            literal = ipaddress.ip_address(hostname)
            if _ip_blocked(literal):
                return False, "Fetching private or internal URLs is not permitted."
            return True, ""
        except ValueError:
            pass

    try:
        pick_public_ip(hostname or "")
    except ValueError as exc:
        return False, str(exc)

    return True, ""


@contextlib.contextmanager
def pin_hostname(hostname: str, ip: str) -> Iterator[None]:
    """Force subsequent socket resolution for *hostname* to *ip*."""
    original = socket.getaddrinfo

    def _patched(host, port, family=0, type=0, proto=0, flags=0):
        if host == hostname:
            return original(ip, port, family, type, proto, flags)
        return original(host, port, family, type, proto, flags)

    socket.getaddrinfo = _patched
    try:
        yield
    finally:
        socket.getaddrinfo = original


def sync_fetch_response(
    url: str,
    *,
    headers: dict[str, str],
    timeout: float = 20.0,
    max_redirects: int = 5,
):
    """Fetch with DNS pinning (sync). Returns an httpx.Response."""
    import httpx

    current = url
    for _ in range(max_redirects + 1):
        ok, err = validate_fetch_url(current)
        if not ok:
            raise ValueError(err)
        parsed = urlparse(current)
        hostname = parsed.hostname or ""
        pinned_ip = pick_public_ip(hostname)
        with pin_hostname(hostname, pinned_ip):
            with httpx.Client(
                follow_redirects=False,
                timeout=timeout,
            ) as client:
                response = client.get(current, headers=headers)
        if response.status_code in (301, 302, 303, 307, 308):
            location = response.headers.get("location")
            if not location:
                return response
            current = urljoin(current, location)
            continue
        return response
    raise ValueError("Too many redirects")


async def async_fetch_response(
    url: str,
    *,
    headers: dict[str, str],
    timeout: float = 15.0,
    max_redirects: int = 5,
):
    """Fetch with DNS pinning (async). Returns an httpx.Response."""
    import httpx

    current = url
    for _ in range(max_redirects + 1):
        ok, err = validate_fetch_url(current)
        if not ok:
            raise ValueError(err)
        parsed = urlparse(current)
        hostname = parsed.hostname or ""
        pinned_ip = pick_public_ip(hostname)
        with pin_hostname(hostname, pinned_ip):
            async with httpx.AsyncClient(
                follow_redirects=False,
                timeout=timeout,
            ) as client:
                response = await client.get(current, headers=headers)
        if response.status_code in (301, 302, 303, 307, 308):
            location = response.headers.get("location")
            if not location:
                return response
            current = urljoin(current, location)
            continue
        return response
    raise ValueError("Too many redirects")
