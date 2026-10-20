"""Node/Vite build step for Lab React apps.

The generated React project lives under the "__src__/" prefix in the app's file
store. To produce the static assets that Lab actually serves, we run
`vite build` inside the dedicated Node sandbox and upload the resulting
`dist/**` to the app root (the keys the serving layer reads).

The Node sandbox image is the standard python-runtime sandbox + Node/npm, so the
existing executor (which dispatches `python3 -c ...`) still works: a small Python
orchestrator writes the sources, runs npm/vite via subprocess with
`--ignore-scripts` (supply-chain hardening), then prints the built files back as a
base64 JSON manifest on stdout.
"""

from __future__ import annotations

import json
import logging
import os
import tempfile
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)

_SRC_PREFIX = "__src__/"
_NODE_POOL = os.environ.get("NODE_BUILD_POOL", "node-build-pool")
_NODE_TEMPLATE = os.environ.get("NODE_BUILD_TEMPLATE", "node-build-executor")
_BUILD_TIMEOUT_S = int(os.environ.get("NODE_BUILD_TIMEOUT_S", "480"))
_MAX_BUILD_STDOUT = 4_000_000  # built bundles are returned inline as base64

# Files that should never be served / are not part of the React source build.
_NON_SOURCE_ROOT = {"server.py"}


def collect_source_files(app_id: str) -> dict[str, str]:
    """Read all __src__/** files for an app, returning {project_rel_path: content}.

    project_rel_path is relative to the React project root (prefix stripped).
    """
    from api_apps import _list_app_files, _read_app_file  # local import to avoid cycle
    out: dict[str, str] = {}
    for rel in _list_app_files(app_id):
        if not rel.startswith(_SRC_PREFIX):
            continue
        proj_rel = rel[len(_SRC_PREFIX):]
        if not proj_rel:
            continue
        try:
            out[proj_rel] = _read_app_file(app_id, rel)
        except Exception:
            logger.warning("node_build: could not read %s", rel)

    # Vite resolves .jsx before .tsx — drop stale JSX stubs when a TSX sibling exists.
    keys = list(out.keys())
    for path in keys:
        if path.endswith(".jsx"):
            tsx = path[:-4] + ".tsx"
            if tsx in out:
                logger.info("node_build: skipping %s (shadowed by %s)", path, tsx)
                del out[path]
    return out


def _sanitize_vite_config(content: str) -> str:
    """Strip AI-generated mistakes from vite.config.{js,ts} that break the build.

    The coder sees files under __src__/ and sometimes sets root: '__src__' thinking
    that's the Vite project root. But node_build strips the prefix before writing to
    /tmp/proj, so the real root is already '.'. We also strip outDir pointing outside
    the project (e.g. '../dist') since we collect dist/ relative to PROJ.
    """
    import re
    # Remove root: '...' or root: "..."
    content = re.sub(r"\broot\s*:\s*['\"][^'\"]*['\"]\s*,?\s*\n?", "", content)
    # Normalise outDir: '../dist' or 'dist' → always 'dist'
    content = re.sub(
        r"(outDir\s*:\s*)['\"][^'\"]*['\"]",
        r"\g<1>'dist'",
        content,
    )
    return content


def _orchestrator_code(source_files: dict[str, str], base_path: str) -> str:
    """Python code (run inside the Node sandbox) that writes sources and builds."""
    payload = json.dumps(source_files)
    return f"""
import base64, json, os, subprocess, sys, shutil

PROJ = '/tmp/proj'
shutil.rmtree(PROJ, ignore_errors=True)
os.makedirs(PROJ, exist_ok=True)

files = json.loads({payload!r})
for rel, content in files.items():
    rel = rel.lstrip('/')
    dest = os.path.join(PROJ, rel)
    os.makedirs(os.path.dirname(dest) or PROJ, exist_ok=True)
    with open(dest, 'w', encoding='utf-8') as fh:
            fh.write(content)

os.chdir(PROJ)

# npm defaults to ~/.npm; sandboxes run as uid 65532 with no writable HOME.
os.environ['HOME'] = '/tmp'
_npm_cache = os.path.join('/tmp', '.npm-cache')
os.environ['npm_config_cache'] = _npm_cache
os.makedirs(_npm_cache, exist_ok=True)

# Prefer a pre-baked node_modules from the image to skip a cold install.
TEMPLATE_MODULES = '/opt/vite-template/node_modules'
if not os.path.isdir(os.path.join(PROJ, 'node_modules')) and os.path.isdir(TEMPLATE_MODULES):
    try:
        shutil.copytree(TEMPLATE_MODULES, os.path.join(PROJ, 'node_modules'), symlinks=True)
    except Exception as e:
        print('WARN copy template node_modules failed:', e, file=sys.stderr)

def run(cmd, timeout):
    env = os.environ.copy()
    env.setdefault('HOME', '/tmp')
    env.setdefault('npm_config_cache', _npm_cache)
    p = subprocess.run(cmd, shell=True, capture_output=True, text=True, timeout=timeout, env=env)
    return p.returncode, p.stdout, p.stderr

# Always run npm install so packages declared in the project's package.json that are
# absent from the pre-baked template (e.g. tailwindcss, new UI libs) get installed.
# --prefer-offline uses the template cache for already-present packages; only
# fetches what's genuinely missing, keeping cold-start overhead minimal.
if os.path.exists(os.path.join(PROJ, 'package-lock.json')) and not os.path.isdir(os.path.join(PROJ, 'node_modules')):
    rc, out, err = run('npm ci --ignore-scripts --no-audit --no-fund', 300)
else:
    rc, out, err = run('npm install --prefer-offline --ignore-scripts --no-audit --no-fund', 300)
if rc != 0:
    print(json.dumps({{'ok': False, 'stage': 'install', 'stderr': (err or out)[-4000:]}}))
    sys.exit(0)

# Use vite directly (pre-baked on PATH via /opt/vite-template/node_modules/.bin).
# Falls back to npx for images that don't have the PATH tweak.
vite_cmd = 'vite build --base={base_path} 2>&1 || npx vite build --base={base_path}'
rc, out, err = run(vite_cmd, {_BUILD_TIMEOUT_S - 60})
if rc != 0:
    print(json.dumps({{'ok': False, 'stage': 'build', 'stderr': (err or out)[-4000:]}}))
    sys.exit(0)

DIST = os.path.join(PROJ, 'dist')
dist = {{}}
if os.path.isdir(DIST):
    for root, _dirs, fnames in os.walk(DIST):
        for fn in fnames:
            fp = os.path.join(root, fn)
            rel = os.path.relpath(fp, DIST)
            try:
                with open(fp, 'rb') as fh:
                    dist[rel] = base64.b64encode(fh.read()).decode()
            except Exception as e:
                print('WARN read dist file failed:', rel, e, file=sys.stderr)

print(json.dumps({{'ok': True, 'dist': dist}}))
"""


async def build_react(app_id: str, slug: str, source_files: Optional[dict[str, str]] = None) -> dict:
    """Build the React project for *app_id* and return the dist manifest.

    Returns: {"ok": bool, "stage": str, "stderr": str, "dist": {rel: text_or_b64}}
    On success, dist values are base64-encoded file bytes.
    """
    from executor import execute_in_sandbox  # local import

    if source_files is None:
        source_files = collect_source_files(app_id)
    if not source_files or not any(k in ("package.json",) for k in source_files):
        return {"ok": False, "stage": "scaffold", "stderr": "No React project (package.json missing under __src__/).", "dist": {}}

    # Sanitize vite configs before sending to sandbox (fixes AI-generated root/outDir mistakes).
    for cfg_name in ("vite.config.ts", "vite.config.js", "vite.config.mjs"):
        if cfg_name in source_files:
            source_files[cfg_name] = _sanitize_vite_config(source_files[cfg_name])

    base_path = f"/apps/{slug}/"
    code = _orchestrator_code(source_files, base_path)

    tmp_dir = Path(tempfile.mkdtemp(prefix=f"nodebuild_{app_id[:8]}_"))
    try:
        result = await execute_in_sandbox(
            code,
            str(tmp_dir),
            timeout=_BUILD_TIMEOUT_S,
            pool=_NODE_POOL,
            template=_NODE_TEMPLATE,
            max_stdout=_MAX_BUILD_STDOUT,
        )
        stdout = (result.get("stdout") or "").strip()
        parsed = None
        # The manifest is the last JSON line printed.
        for line in reversed(stdout.splitlines()):
            line = line.strip()
            if line.startswith("{"):
                try:
                    parsed = json.loads(line)
                    break
                except ValueError:
                    continue
        if parsed is None:
            return {"ok": False, "stage": "build", "stderr": (result.get("stderr") or stdout)[-4000:], "dist": {}}
        parsed.setdefault("dist", {})
        return parsed
    except Exception as exc:  # noqa: BLE001
        logger.exception("node_build: build failed for app %s", app_id)
        return {"ok": False, "stage": "exception", "stderr": str(exc)[:2000], "dist": {}}
    finally:
        import shutil
        shutil.rmtree(str(tmp_dir), ignore_errors=True)


def upload_dist(app_id: str, dist_b64: dict[str, str]) -> list[str]:
    """Write built (base64) dist files to the app root so the serving layer picks them up."""
    import base64 as _b64
    from api_apps import _write_app_file
    written: list[str] = []
    for rel, b64 in dist_b64.items():
        rel = rel.replace("\\", "/").lstrip("/")
        if not rel or rel.startswith(_SRC_PREFIX) or rel in _NON_SOURCE_ROOT:
            continue
        try:
            content = _b64.b64decode(b64).decode("utf-8", errors="replace")
        except Exception:
            logger.warning("node_build: could not decode dist file %s", rel)
            continue
        try:
            _write_app_file(app_id, rel, content)
            written.append(rel)
        except Exception:
            logger.warning("node_build: could not write dist file %s", rel)
    return written
