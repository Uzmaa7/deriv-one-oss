"""Post-build smoke verification for Lab apps."""

from __future__ import annotations

import ast
import logging
from typing import Optional

logger = logging.getLogger(__name__)


def _file_exists(app_id: str, rel: str) -> bool:
    from api_apps import _list_app_files
    return rel in _list_app_files(app_id)


def _read_safe(app_id: str, rel: str) -> Optional[str]:
    from api_apps import _read_app_file
    try:
        return _read_app_file(app_id, rel)
    except Exception:
        return None


def verify_server_py(app_id: str) -> list[str]:
    """Parse-check server.py if present."""
    errors: list[str] = []
    if not _file_exists(app_id, "server.py"):
        return errors
    content = _read_safe(app_id, "server.py")
    if content is None:
        errors.append("server.py exists but could not be read.")
        return errors
    try:
        ast.parse(content)
    except SyntaxError as exc:
        errors.append(f"server.py syntax error: {exc}")
        return errors
    # Local helper modules imported by server.py must exist at app root (e.g. data.py).
    import re
    for match in re.finditer(r"^\s*(?:from|import)\s+([a-zA-Z_][a-zA-Z0-9_]*)", content, re.MULTILINE):
        mod = match.group(1)
        if mod in ("os", "json", "re", "time", "datetime", "typing", "requests", "math", "random"):
            continue
        if mod == "server":
            continue
        if not _file_exists(app_id, f"{mod}.py"):
            errors.append(f"server.py imports '{mod}' but {mod}.py is missing at app root.")
    return errors


def verify_react_routing(app_id: str) -> list[str]:
    """Ensure React apps keep routes under /apps/{slug}/ via BrowserRouter basename."""
    errors: list[str] = []
    main = _read_safe(app_id, "__src__/src/main.tsx")
    if main is None:
        return errors
    if "BrowserRouter" not in main:
        errors.append("main.tsx must wrap App in BrowserRouter with basename from import.meta.env.BASE_URL.")
    elif "basename" not in main or "BASE_URL" not in main:
        errors.append(
            "main.tsx BrowserRouter must set basename={import.meta.env.BASE_URL.replace(/\\/$/, '') || '/'} "
            "so routes stay under /apps/{slug}/."
        )

    from api_apps import _list_app_files
    import re
    for rel in _list_app_files(app_id):
        if not rel.startswith("__src__/") or not rel.endswith((".tsx", ".jsx", ".ts", ".js")):
            continue
        content = _read_safe(app_id, rel)
        if not content:
            continue
        if rel != "__src__/src/main.tsx" and "<BrowserRouter" in content:
            if "basename" not in content:
                errors.append(
                    f"{rel}: remove extra BrowserRouter or add basename — only main.tsx should own the router."
                )
        if re.search(r"""href\s*=\s*["']/[^"']*["']""", content):
            errors.append(
                f"{rel}: use <Link to=\"...\"> instead of href=\"/...\" — absolute href escapes the lab app."
            )
        if re.search(r"""window\.location(?:\.href)?\s*=\s*["']/""", content):
            errors.append(f"{rel}: do not set window.location to a site-root path; use React Router navigate().")
    return errors


def verify_app_js_run_contract(app_id: str) -> list[str]:
    """Warn when app.js uses fake REST paths instead of the Lab /run contract."""
    errors: list[str] = []
    js = _read_safe(app_id, "app.js")
    if not js:
        return errors
    import re
    if re.search(r"""fetch\s*\(\s*['"`]/api/(?!apps/)""", js):
        errors.append(
            "app.js fetches /api/... REST paths; Lab apps must use runPython() "
            "(POST /api/apps/{app_id}/run)."
        )
    return errors


async def verify_lab_app(
    app_id: str,
    tech_stack: str,
    slug: str,
    *,
    skip_build: bool = False,
) -> dict:
    """Run smoke checks after a build. Returns {ok, errors}."""
    errors: list[str] = []

    if tech_stack == "react":
        if not skip_build:
            from node_build import build_react, upload_dist
            result = await build_react(app_id, slug)
            if not result.get("ok"):
                stage = result.get("stage", "build")
                stderr = (result.get("stderr") or "")[:2000]
                errors.append(f"React build failed ({stage}): {stderr}")
            elif result.get("dist"):
                from node_build import upload_dist
                upload_dist(app_id, result["dist"])

        if not _file_exists(app_id, "index.html"):
            errors.append("Missing index.html after React build.")
        errors.extend(verify_react_routing(app_id))
    else:
        if not _file_exists(app_id, "index.html"):
            errors.append("Missing index.html — app has no entry point.")

    errors.extend(verify_server_py(app_id))
    errors.extend(verify_app_js_run_contract(app_id))

    return {"ok": len(errors) == 0, "errors": errors}
