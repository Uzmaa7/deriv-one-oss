"""Isolate Lab documents from the platform session.

Served Lab HTML is a unique browser origin (CSP sandbox, no allow-same-origin).
It cannot attach the platform cookie. Runtime calls carry a per-app capability
instead, and only the data and run routes accept that capability.
"""

from __future__ import annotations

import json
import os
import re
from urllib.parse import urlparse

from platform_client_guard import is_valid_platform_client

_RUNTIME_RE = re.compile(
    r"^/api/apps/(?P<app_id>[A-Za-z0-9_-]{8,80})"
    r"/(?:me|run|records(?:/[^/]+){0,2}|record-collections/[^/]+)/?$"
)
_SLUG_RE = re.compile(r"^[a-z0-9][a-z0-9-]{0,80}$")
_APP_ID_RE = re.compile(r"^[A-Za-z0-9_-]{8,80}$")
_ORIGIN_RE = re.compile(r"^https?://[A-Za-z0-9._:-]+$")

_BROWSER_FETCH_MODES = frozenset({"cors", "no-cors", "same-origin", "websocket"})

_SENSITIVE_API_PREFIXES = (
    "/api/admin/",
    "/api/setup",
    "/api/platform-setup",
)


def lab_app_public_origin(request_base_url: str) -> str:
    """Browser origin used in Lab CSP. Prefer APP_BASE_URL when it is absolute."""
    configured = os.environ.get("APP_BASE_URL", "").strip().rstrip("/")
    if configured.startswith(("http://", "https://")):
        return configured
    return (request_base_url or "").strip().rstrip("/")


def lab_runtime_app_id(path: str) -> str | None:
    """App id when *path* is a Lab data or run route. None for every other API."""
    match = _RUNTIME_RE.match(path or "")
    if not match:
        return None
    return match.group("app_id")


def lab_app_content_security_policy(origin: str, slug: str, app_id: str) -> str:
    """Unique-origin CSP. Scripts may load only this app. Fetch may call only its API."""
    root = origin.rstrip("/")
    if not (
        _ORIGIN_RE.fullmatch(root)
        and _SLUG_RE.fullmatch(slug or "")
        and _APP_ID_RE.fullmatch(app_id or "")
    ):
        return "sandbox; default-src 'none'; frame-ancestors 'none'"
    assets = f"{root}/apps/{slug}/"
    api = f"{root}/api/apps/{app_id}/"
    return (
        "sandbox allow-scripts allow-forms allow-modals allow-popups allow-downloads; "
        "default-src 'none'; "
        f"base-uri {assets}; "
        f"form-action {api}; "
        f"frame-ancestors {root}; "
        "object-src 'none'; "
        "worker-src 'none'; "
        f"script-src 'unsafe-inline' {assets}; "
        f"style-src 'unsafe-inline' {assets}; "
        f"img-src {assets} data: blob:; "
        f"font-src {assets} data:; "
        f"connect-src {api}"
    )


def lab_app_response_headers(request_base_url: str, slug: str, app_id: str) -> dict[str, str]:
    origin = lab_app_public_origin(request_base_url)
    return {
        "Content-Security-Policy": lab_app_content_security_policy(origin, slug, app_id),
        "X-Content-Type-Options": "nosniff",
        "Referrer-Policy": "no-referrer",
    }


def inject_lab_runtime(html: bytes, app_id: str, token: str) -> bytes:
    """Patch fetch before app scripts run so existing apps send the capability."""
    bootstrap = (
        "<script>"
        f"(()=>{{const appId={json.dumps(app_id)};const token={json.dumps(token)};"
        "const native=window.fetch.bind(window);"
        "const allowed=(url)=>{try{const u=new URL(url,window.location.href);"
        "return u.pathname.startsWith('/api/apps/'+appId+'/');}catch(e){return false;}};"
        "window.fetch=function(input,init){const url=typeof input==='string'?input:"
        "(input&&input.url)||'';"
        "if(typeof url==='string'&&url.includes('/api/')&&!allowed(url))"
        "{return Promise.reject(new Error('This API is not available to Lab apps.'));}"
        "const next=Object.assign({},init);"
        "const headers=new Headers(next.headers||(typeof input!=='string'&&input?input.headers:undefined));"
        "if(allowed(url)){headers.set('X-Lab-Capability',token);next.headers=headers;next.credentials='omit';}"
        "return native(input,next);};"
        "})();"
        "</script>"
    )
    raw = html.decode("utf-8", errors="replace")
    lower = raw.lower()
    idx = lower.find("<head>")
    if idx != -1:
        at = idx + len("<head>")
        raw = raw[:at] + bootstrap + raw[at:]
    else:
        raw = bootstrap + raw
    return raw.encode("utf-8")


def lab_cors_headers() -> dict[str, str]:
    return {
        "Access-Control-Allow-Origin": "null",
        "Access-Control-Allow-Headers": "content-type, x-lab-capability",
        "Access-Control-Allow-Methods": "GET, POST, PUT, DELETE, OPTIONS",
        "Vary": "Origin",
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
    method: str = "GET",
) -> bool:
    """Browser calls that are not the platform UI and not a Lab /api/apps route.

    Origin never includes a path, and Lab responses send no Referer, so those
    headers cannot identify an app page. A same-origin fetch or form post
    without the platform UI header is treated as untrusted. Server-to-server
    calls, which send neither Origin nor Sec-Fetch-Site, are left alone.
    """
    if is_valid_platform_client(platform_client_header):
        return False
    if request_from_lab_app_sandbox(origin, referer):
        return True
    site = (sec_fetch_site or "").strip().lower()
    mode = (sec_fetch_mode or "").strip().lower()
    if site == "same-origin" and mode in _BROWSER_FETCH_MODES:
        return True
    if site == "same-origin" and mode == "navigate" and method.upper() not in ("GET", "HEAD"):
        return True
    if origin and site in ("", "same-origin") and mode in _BROWSER_FETCH_MODES:
        return True
    return False
