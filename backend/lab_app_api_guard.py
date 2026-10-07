"""Restrict API access from Lab app preview/runtime origins.

Lab apps are served from /apps/{slug}/ on the platform origin. Malicious app
JavaScript must not use the viewer's session cookie to call unrelated platform
APIs. Requests whose Origin or Referer indicate a Lab app context may only reach
/api/apps/* routes. Browser-enforced CSP on served lab assets is the primary
control; this module provides server-side enforcement as defense in depth.
"""

from __future__ import annotations

from urllib.parse import urlparse

from platform_client_guard import is_valid_platform_client

# Restrict connect targets for untrusted lab HTML/JS (browser-enforced).
LAB_APP_CONTENT_SECURITY_POLICY = (
    "default-src 'self'; "
    "base-uri 'self'; "
    "form-action 'self'; "
    "frame-ancestors 'self'; "
    "object-src 'none'; "
    "script-src 'self' 'unsafe-inline'; "
    "style-src 'self' 'unsafe-inline'; "
    "img-src 'self' data: blob:; "
    "font-src 'self' data:; "
    "connect-src 'self' /api/apps/; "
    "upgrade-insecure-requests"
)

_SENSITIVE_API_PREFIXES = (
    "/api/admin/",
    "/api/setup",
    "/api/platform-setup",
)


def lab_app_response_headers() -> dict[str, str]:
    return {
        "Content-Security-Policy": LAB_APP_CONTENT_SECURITY_POLICY,
        "X-Content-Type-Options": "nosniff",
        "Referrer-Policy": "no-referrer",
    }


def _header_indicates_lab_app(header_value: str | None) -> bool:
    if not header_value:
        return False
    parsed = urlparse(header_value)
    path = parsed.path or header_value
    return "/apps/" in path


def request_from_lab_app_sandbox(origin: str | None, referer: str | None) -> bool:
    return _header_indicates_lab_app(origin) or _header_indicates_lab_app(referer)


def lab_app_api_path_allowed(path: str) -> bool:
    return path.startswith("/api/apps/")


def is_sensitive_platform_api(path: str) -> bool:
    return any(path.startswith(prefix) for prefix in _SENSITIVE_API_PREFIXES)


def untrusted_same_origin_api_call(
    *,
    path: str,
    origin: str | None,
    referer: str | None,
    platform_client_header: str | None,
    sec_fetch_site: str | None,
    sec_fetch_mode: str | None,
) -> bool:
    """Detect fetch() from a lab app document that omits Referer/Origin."""
    if is_valid_platform_client(platform_client_header):
        return False
    if request_from_lab_app_sandbox(origin, referer):
        return True
    if not is_sensitive_platform_api(path):
        return False
    site = (sec_fetch_site or "").strip().lower()
    mode = (sec_fetch_mode or "").strip().lower()
    if site == "same-origin" and mode in ("cors", "no-cors", "same-origin"):
        return True
    if not referer and not origin and site == "same-origin":
        return True
    return False
