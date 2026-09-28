"""Minimal sandbox HTTP server — mirrors the a stronger container runtime runtime API.

The sandbox client sends:
  POST /execute  {"command": "python3 -c \"...\""}
  → {"stdout": str, "stderr": str, "exit_code": int}

Also exposes legacy POST / for local-dev compatibility.
"""
import asyncio
import os
import sys
import traceback
from fastapi import FastAPI, Request

app = FastAPI()


@app.get("/")
async def health():
    return {"status": "ok"}


async def _run_command(command: str, timeout: int, env: dict) -> dict:
    """Run a shell command and return {stdout, stderr, exit_code}."""
    try:
        proc = await asyncio.create_subprocess_shell(
            command,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            env=env,
        )
        stdout_b, stderr_b = await asyncio.wait_for(proc.communicate(), timeout=timeout)
        return {
            "stdout": stdout_b.decode("utf-8", errors="replace"),
            "stderr": stderr_b.decode("utf-8", errors="replace"),
            "exit_code": proc.returncode,
        }
    except asyncio.TimeoutError:
        try:
            proc.kill()
        except Exception:
            pass
        return {"stdout": "", "stderr": "Timeout exceeded", "exit_code": -1}
    except Exception:
        return {"stdout": "", "stderr": traceback.format_exc(), "exit_code": -1}


@app.post("/execute")
async def execute(request: Request):
    """Primary endpoint called by the sandbox client's command executor."""
    body = await request.json()
    command = body.get("command", "")
    timeout = int(body.get("timeout", 90))
    env = {**os.environ}
    return await _run_command(command, timeout, env)


@app.post("/")
async def run_code(request: Request):
    """Legacy endpoint: accepts {"code": "..."} and runs it via python3 -c."""
    body = await request.json()
    code = body.get("code", "")
    timeout = int(body.get("timeout", 90))
    env_extras = body.get("env", {}) or {}
    env = {**os.environ, **{str(k): str(v) for k, v in env_extras.items()}}
    command = f"{sys.executable} -c {code!r}"
    result = await _run_command(command, timeout, env)
    # Legacy callers expect "returncode" not "exit_code"
    result["returncode"] = result.pop("exit_code")
    return result
