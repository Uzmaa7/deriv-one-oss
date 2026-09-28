"""Run model-generated code in a sandbox.

Delegates to the configured :class:`~providers.sandbox.SandboxProvider`, so
what "sandbox" means — a disposable container, a stronger runtime, a local
process — is a deployment decision rather than something baked in here. What
this module owns is everything around the execution:

  * building the script, with a prolog that fixes the working directory,
    exposes the output directories and injects only the secrets that were
    explicitly requested;
  * redacting anything secret-shaped from stdout before it is returned;
  * pulling generated files out of the output, both from inline markers and
    from files the run left behind;
  * retrying transient acquisition failures, and truncating oversized output.

Whatever the provider, the guarantees callers rely on are: no ambient
credentials, no network unless granted, and a fresh environment per run. Each
provider states what it actually enforces in its ``isolation`` attribute, which
is surfaced to operators — see ``providers/sandbox.py``.

Usage:
    # Async (request handler):
    result = await execute_in_sandbox(code, workspace_dir="/data/users/abc")

    # Sync (scheduler or background thread):
    output = execute_in_sandbox_sync(code, workspace_dir="/data/users/abc")
"""

from __future__ import annotations

import asyncio
import base64
import logging
import os
import re
from typing import Any
from providers import get_sandbox
from providers.sandbox import SandboxSpec
from pathlib import Path

logger = logging.getLogger(__name__)

_SANDBOX_NAMESPACE = os.environ.get("SANDBOX_NAMESPACE", "agent-sandbox")
_EXEC_TIMEOUT_S = 90   # max wall-clock seconds for user code inside sandbox
_SANDBOX_CONNECT_RETRIES = max(1, int(os.environ.get("SANDBOX_CONNECT_RETRIES", "3")))
_SANDBOX_RETRY_BASE_DELAY_S = float(os.environ.get("SANDBOX_RETRY_BASE_DELAY_S", "1.0"))
# Tool output handed back to the model. Tail-only truncation silently drops the
# head of a large stdout, so the model sees a fragment starting mid-token and
# re-runs the same query narrower — burning tool turns. Keep head + tail and say
# what was dropped instead.
_DEFAULT_MAX_STDOUT = max(1_000, int(os.environ.get("SANDBOX_MAX_STDOUT_CHARS", "12000")))
_DEFAULT_MAX_STDERR = max(500, int(os.environ.get("SANDBOX_MAX_STDERR_CHARS", "2000")))

# Patterns to redact from sandbox output before returning to Claude.
# These are defence-in-depth; the sandbox has no access to backend env,
# but user code could print values it received as skill secrets.
# ── Generated-file extraction ─────────────────────────────────────────────────
# Sandbox code can pipe binary files back to the backend by printing:
#   print(f"<|FILE|>filename.docx:{base64_data}<|ENDFILE|>")
# The backend extracts these markers from stdout, saves the files to the user's
# workspace on the *backend* filesystem, and strips the markers from the output
# shown to the model.
_FILE_MARKER_RE = re.compile(
    r"<\|FILE\|>([^:\n<>|]+):([A-Za-z0-9+/=\n\r]+)<\|ENDFILE\|>",
    re.DOTALL,
)

_EXTENSION_MIME: dict[str, str] = {
    ".docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    ".xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    ".pptx": "application/vnd.openxmlformats-officedocument.presentationml.presentation",
    ".pdf":  "application/pdf",
    ".csv":  "text/csv",
    ".txt":  "text/plain",
    ".png":  "image/png",
    ".jpg":  "image/jpeg",
    ".jpeg": "image/jpeg",
}


def _extract_generated_files(
    stdout: str,
    workspace_dir: str,
) -> tuple[str, list[dict]]:
    """Parse FILE markers from *stdout*, save files to *workspace_dir*.

    Returns ``(cleaned_stdout, files_info)`` where *files_info* is a list of
    ``{"filename": str, "size": int, "mime": str}`` dicts for each saved file.
    Markers are stripped from the returned stdout so they are not shown to the
    model or displayed in the UI.
    """
    import pathlib

    files_info: list[dict] = []
    work_path = pathlib.Path(workspace_dir)
    work_path.mkdir(parents=True, exist_ok=True)

    def _save(match: re.Match) -> str:
        raw_name = match.group(1).strip()
        b64data = match.group(2).strip().replace("\n", "").replace("\r", "")
        safe_name = pathlib.Path(raw_name).name
        if not safe_name:
            return ""
        dest = work_path / safe_name
        # Prevent path traversal: dest must stay inside workspace_dir
        try:
            dest.resolve().relative_to(work_path.resolve())
        except ValueError:
            return ""
        try:
            data = base64.b64decode(b64data)
        except Exception:
            return ""
        dest.write_bytes(data)
        ext = pathlib.Path(safe_name).suffix.lower()
        mime = _EXTENSION_MIME.get(ext, "application/octet-stream")
        files_info.append({"filename": safe_name, "size": len(data), "mime": mime})
        return ""

    cleaned = _FILE_MARKER_RE.sub(_save, stdout)
    return cleaned.strip(), files_info


_SENSITIVE_PATTERNS = [
    (re.compile(r'(?i)(password|secret|token|api[_\-]?key|private[_\-]?key)\s*[:=]\s*\S+'), "[REDACTED-SECRET]"),
    (re.compile(r'postgresql(\+asyncpg)?://[^\s"\'<>]+'), "[REDACTED-DB-URL]"),
    (re.compile(r'(xoxb|xoxp|xapp)-[A-Za-z0-9\-]{10,}'), "[REDACTED-SLACK-TOKEN]"),
    (re.compile(r'sk-[A-Za-z0-9]{20,}'), "[REDACTED-API-KEY]"),
    (re.compile(r'AIza[0-9A-Za-z\-_]{35}'), "[REDACTED-API-KEY]"),
    (re.compile(r'AKIA[0-9A-Z]{16}'), "[REDACTED-AWS-KEY]"),
]


def _sanitize(output: str) -> str:
    from redaction import redact_text

    return redact_text(output)


_ARTIFACTS_DIR = "/tmp/_artifacts"


def _build_code(
    user_code: str,
    workspace_dir: str,
    env_extras: dict[str, str] | None,
    artifacts: dict[str, str] | None = None,
) -> str:
    """Prepend a safe prolog and return the final script to execute.

    The prolog:
      - Sets the working directory to a writable /tmp path inside the sandbox.
      - Exposes OUTPUT_DIR / PDF_OUTPUT_DIR so generated files land there.
      - Optionally injects skill secrets as env vars (only the requested keys).
      - Does NOT add any backend module to sys.path.

    Note: workspace_dir is a path on the *backend* pod; the sandbox has its own
    filesystem and can only write to /tmp. We use the last component of the path
    as a stable per-user subdirectory under /tmp.
    """
    import os.path as _osp
    sandbox_workspace = f"/tmp/{_osp.basename(workspace_dir)}"

    env_block = ""
    if env_extras:
        for k, v in env_extras.items():
            # Encode values so newlines / quotes don't break the prolog.
            b64 = base64.b64encode(v.encode()).decode()
            env_block += (
                f"import base64 as _b64\n"
                f"import os as _os\n"
                f"_os.environ[{k!r}] = _b64.b64decode({b64!r}).decode()\n"
            )
    artifact_block = ""
    if artifacts:
        artifact_block += (
            "import base64 as _b64\n"
            "import os as _os\n"
            "import sys as _sys\n"
            f"_os.makedirs({_ARTIFACTS_DIR!r}, exist_ok=True)\n"
        )
        for mod, src in artifacts.items():
            safe_mod = "".join(c for c in mod if c.isalnum() or c == "_")
            if not safe_mod or safe_mod[0].isdigit():
                continue
            blob = base64.b64encode(src.encode()).decode()
            artifact_block += (
                f"open({_ARTIFACTS_DIR + '/' + safe_mod + '.py'!r}, 'wb')"
                f".write(_b64.b64decode({blob!r}))\n"
            )
        artifact_block += f"_sys.path.insert(0, {_ARTIFACTS_DIR!r})\n"

    workspace_download_block = ""
    if env_extras and "WORKSPACE_FILES" in env_extras:
        workspace_download_block = (
            "import json as _json, os as _wos, requests as _requests\n"
            "_WORKSPACE_DIR = '/tmp/_workspace'\n"
            "_wos.makedirs(_WORKSPACE_DIR, exist_ok=True)\n"
            "try:\n"
            "    _wf = _json.loads(_wos.environ.get('WORKSPACE_FILES', '{}'))\n"
            "    for _wname, _wurl in _wf.items():\n"
            "        _wdest = _wos.path.join(_WORKSPACE_DIR, _wos.path.basename(_wname))\n"
            "        if not _wos.path.exists(_wdest):\n"
            "            _wr = _requests.get(_wurl, timeout=120, stream=True)\n"
            "            _wr.raise_for_status()\n"
            "            with open(_wdest, 'wb') as _wf2:\n"
            "                for _wchunk in _wr.iter_content(65536):\n"
            "                    _wf2.write(_wchunk)\n"
            "    WORKSPACE_DIR = _WORKSPACE_DIR\n"
            "except Exception as _we:\n"
            "    import sys as _wsys\n"
            "    print(f'[workspace-prolog] download warning: {_we}', file=_wsys.stderr)\n"
            "    WORKSPACE_DIR = _WORKSPACE_DIR\n"
        )

    prolog = (
        "import os as _os\n"
        f"_os.makedirs({sandbox_workspace!r}, exist_ok=True)\n"
        f"_os.chdir({sandbox_workspace!r})\n"
        f"PDF_OUTPUT_DIR = OUTPUT_DIR = {sandbox_workspace!r}\n"
        + env_block
        + workspace_download_block
        + artifact_block
    )
    return prolog + user_code


_sandbox_client: Any = None
_sandbox_client_loop: Any = None
# Lock is created lazily to avoid binding to wrong/no event loop at import time.
_client_lock: asyncio.Lock | None = None


def _get_lock() -> asyncio.Lock:
    global _client_lock
    if _client_lock is None:
        _client_lock = asyncio.Lock()
    return _client_lock


def _is_retryable_sandbox_error(exc: BaseException) -> bool:
    """Return True for transient sandbox infra failures worth retrying."""
    from providers.sandbox import SandboxError

    if isinstance(exc, SandboxError):
        return True
    try:
        import httpx  # type: ignore[import]
        if isinstance(exc, httpx.ConnectError):
            return True
    except ImportError:
        pass
    msg = str(exc).lower()
    return (
        "name or service not known" in msg
        or "failed to communicate with the sandbox" in msg
        or "connection refused" in msg
        or "connection reset" in msg
    )


def truncate_tool_output(text: str, limit: int, *, keep: str = "middle") -> str:
    """Cap *text* at *limit* chars, stating explicitly what was dropped.

    ``keep="middle"`` retains the head and tail (stdout: the header of a payload
    is as load-bearing as its end). ``keep="tail"`` retains only the end
    (stderr: a traceback's final frames matter most).
    """
    if limit <= 0 or len(text) <= limit:
        return text
    omitted = len(text) - limit
    marker = (
        f"\n\n… [{omitted} characters omitted — print less, filter or aggregate "
        f"inside the sandbox, or page through the source instead of re-running] …\n\n"
    )
    budget = limit - len(marker)
    if budget <= 0:
        return text[:limit]
    if keep == "tail":
        return marker.lstrip("\n") + text[-budget:]
    head = budget // 2
    return text[:head] + marker + text[-(budget - head):]


async def execute_in_sandbox(
    code: str,
    workspace_dir: str,
    env_extras: dict[str, str] | None = None,
    timeout: int = _EXEC_TIMEOUT_S,
    pool: str | None = None,
    template: str | None = None,
    max_stdout: int = _DEFAULT_MAX_STDOUT,
    artifacts: dict[str, str] | None = None,
) -> dict[str, Any]:
    """Execute *code* in a fresh sandbox and return stdout/stderr.

    Args:
        code: Python source code to run.
        workspace_dir: Absolute path to the user's workspace directory. The
            sandbox working-directory is set to this path.
        env_extras: Optional dict of extra env vars (skill secrets) to inject
            into the sandbox. Only these keys are exposed.
        timeout: Max execution time in seconds (default 90s).
        pool, template: Unused. Kept in the signature for source compatibility
            with callers written against the pre-provider executor; the
            configured SandboxProvider decides what actually runs.

    Returns:
        {"stdout": str, "stderr": str, "returncode": int}
    """
    full_code = _build_code(code, workspace_dir, env_extras, artifacts=artifacts)

    sandbox = get_sandbox()
    stdout = ""
    stderr = ""
    returncode = 1

    spec = SandboxSpec(
        language="python",
        timeout_seconds=timeout,
        env=dict(env_extras or {}),
    )

    for attempt in range(1, _SANDBOX_CONNECT_RETRIES + 1):
        try:
            result = await sandbox.run(full_code, spec=spec)
            stdout = _sanitize(result.stdout or "")
            stderr = result.stderr or ""
            returncode = result.exit_code
            # Files the run wrote are surfaced the same way as inline markers,
            # so callers do not need to know which mechanism produced them.
            for name, blob in (result.files or {}).items():
                try:
                    target = Path(workspace_dir) / Path(name).name
                    target.parent.mkdir(parents=True, exist_ok=True)
                    target.write_bytes(blob)
                except OSError:
                    logger.warning("executor: could not save produced file %s", name)
            break
        except Exception as exc:
            if attempt < _SANDBOX_CONNECT_RETRIES and _is_retryable_sandbox_error(exc):
                logger.warning(
                    "executor: sandbox attempt %d/%d failed (%s), retrying",
                    attempt,
                    _SANDBOX_CONNECT_RETRIES,
                    exc,
                )
                await asyncio.sleep(_SANDBOX_RETRY_BASE_DELAY_S * attempt)
                continue
            raise

    # Extract any <|FILE|>...<|ENDFILE|> blobs and save them to workspace_dir.
    stdout, files = _extract_generated_files(stdout, workspace_dir)

    return {
        "stdout": truncate_tool_output(stdout, max_stdout),
        "stderr": truncate_tool_output(stderr, _DEFAULT_MAX_STDERR, keep="tail"),
        "returncode": returncode,
        "files": files,
    }


def execute_in_sandbox_sync(
    code: str,
    workspace_dir: str,
    env_extras: dict[str, str] | None = None,
    timeout: int = _EXEC_TIMEOUT_S,
    pool: str | None = None,
    template: str | None = None,
    max_stdout: int = _DEFAULT_MAX_STDOUT,
    artifacts: dict[str, str] | None = None,
) -> str:
    """Synchronous wrapper for APScheduler / background threads.

    Returns combined stdout (+ stderr if non-empty) as a plain string,
    matching the return contract of the old _execute_code_sync().
    """
    result = asyncio.run(
        execute_in_sandbox(
            code, workspace_dir, env_extras, timeout, pool, template, max_stdout, artifacts
        )
    )
    parts = []
    if result["stdout"]:
        parts.append(result["stdout"].strip())
    if result["stderr"]:
        parts.append(f"[stderr]\n{result['stderr'].strip()}")
    return "\n".join(parts) or "(no output)"
