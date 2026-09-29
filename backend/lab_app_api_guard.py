"""Restrict API access from Lab app preview/runtime origins.

Lab apps are served from /apps/{slug}/ on the platform origin. Malicious app
JavaScript must not use the viewer's session cookie to call unrelated platform
APIs. Requests whose Origin or Referer indicate a Lab app context may only reach
/api/apps/* routes.
"""

from __future__ import annotations

from urllib.parse import urlparse


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
