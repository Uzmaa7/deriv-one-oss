"""What runs untrusted code.

Code written by a language model is untrusted code. It runs somewhere that can
be destroyed, that holds no ambient credentials, and that cannot reach anything
the request did not explicitly grant it.

Different deployments can enforce that to different depths, and the difference
matters enough that it is part of the interface: every provider must state its
guarantee in :attr:`SandboxProvider.isolation`, and that string is surfaced in
the UI. An operator should never have to read source code to find out how well
isolated their execution is.

Reference implementations:

``ContainerSandbox``
    One container per run, discarded afterwards. Network off by default, memory
    and CPU capped, filesystem read-only apart from a scratch directory, all
    capabilities dropped, no credentials mounted. If the host has a stronger
    container runtime configured, it is used; otherwise the guarantee is
    ordinary kernel-shared container isolation, and it says so.

``SubprocessSandbox``
    A local process with no isolation whatsoever. For development only, and it
    refuses to start unless explicitly enabled, because the failure mode of
    quietly running model-authored code on a real machine is severe.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import shutil
import socket
import subprocess
import sys
import tempfile
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)

DEFAULT_TIMEOUT_SECONDS = 120
DEFAULT_MEMORY_MB = 1024


def _clean_env(key: str) -> str:
    return (os.environ.get(key) or "").strip()


def _env_bool(key: str) -> bool:
    return _clean_env(key).lower() in ("1", "true", "yes", "on")


def _in_container() -> bool:
    """Whether this process is itself inside a container.

    Only used to turn a confusing runtime failure into a configuration error at
    startup, so a false negative costs nothing that was not already broken.
    """
    if Path("/.dockerenv").exists():
        return True
    try:
        cgroup = Path("/proc/self/cgroup").read_text(encoding="utf-8")
    except OSError:
        return False
    return any(marker in cgroup for marker in ("docker", "containerd", "kubepods"))


def _discover_host_mount_source(engine: str, container_path: str) -> str:
    """Where the engine thinks this container's ``container_path`` comes from.

    Saves the operator from naming the same directory twice. The daemon already
    knows both halves of every mount it set up for us, so ask it rather than
    making a host path a thing that has to be kept in sync by hand.
    """
    try:
        result = subprocess.run(
            [engine, "inspect", "--format", "{{json .Mounts}}", socket.gethostname()],
            capture_output=True,
            text=True,
            timeout=15,
        )
    except (OSError, subprocess.SubprocessError):
        return ""
    if result.returncode != 0:
        return ""
    try:
        mounts = json.loads(result.stdout.strip() or "[]")
    except (ValueError, TypeError):
        return ""
    for mount in mounts or []:
        if isinstance(mount, dict) and mount.get("Destination") == container_path:
            return str(mount.get("Source") or "")
    return ""


def _resolve_run_as_user() -> str:
    """The ``--user`` value for run containers.

    Defaults to this process's own uid/gid so that files a run produces are
    owned by whoever has to read them back and delete the workspace afterwards.
    Without it a root-owned run leaves files this process cannot clean up.
    """
    configured = _clean_env("SANDBOX_RUN_AS_USER")
    if configured.lower() in ("off", "false", "no", "0"):
        return ""
    if configured:
        return configured
    if hasattr(os, "getuid"):
        return f"{os.getuid()}:{os.getgid()}"
    return ""


class SandboxError(RuntimeError):
    """The sandbox could not be created or run."""


@dataclass
class SandboxSpec:
    """What a single run needs."""

    language: str = "python"
    timeout_seconds: int = DEFAULT_TIMEOUT_SECONDS
    memory_mb: int = DEFAULT_MEMORY_MB
    cpu_cores: float = 1.0
    network: bool = False
    env: dict[str, str] = field(default_factory=dict)
    files: dict[str, bytes] = field(default_factory=dict)
    image: Optional[str] = None


@dataclass
class SandboxResult:
    """What a run produced."""

    exit_code: int
    stdout: str
    stderr: str
    duration_ms: int
    timed_out: bool = False
    files: dict[str, bytes] = field(default_factory=dict)

    @property
    def ok(self) -> bool:
        return self.exit_code == 0 and not self.timed_out


class SandboxProvider(ABC):
    """An execution boundary for untrusted code."""

    #: Human-readable statement of what this provider actually guarantees.
    #: Shown to operators. Be honest here; understating risk is a security bug.
    isolation: str = "unspecified"

    @abstractmethod
    async def run(self, code: str, *, spec: SandboxSpec | None = None) -> SandboxResult:
        """Execute ``code`` and return what happened. Must not raise on non-zero exit."""

    async def warm(self, count: int = 1) -> int:
        """Pre-create ``count`` idle sandboxes. Returns how many are ready.

        Optional. Acquisition latency dominates short runs, so providers that
        can prepare execution environments ahead of time should.
        """
        return 0

    async def reap(self) -> int:
        """Destroy sandboxes leaked by a crashed caller. Returns the count."""
        return 0

    async def close(self) -> None:
        """Release provider-level resources."""
        return


def _language_command(language: str) -> list[str]:
    normalised = (language or "python").strip().lower()
    if normalised in ("python", "python3", "py"):
        return [sys.executable or "python3", "main.py"]
    if normalised in ("node", "javascript", "js"):
        return ["node", "main.js"]
    raise SandboxError(f"unsupported sandbox language: {language!r}")


def _entry_filename(language: str) -> str:
    normalised = (language or "python").strip().lower()
    return "main.js" if normalised in ("node", "javascript", "js") else "main.py"


class ContainerSandbox(SandboxProvider):
    """One disposable container per run.

    The container engine is whatever ``CONTAINER_ENGINE`` names (default
    ``docker``); any engine with a compatible command-line works. A stronger
    runtime can be requested with ``SANDBOX_RUNTIME`` — when set, it is passed
    through and named in the isolation statement, and when unset the guarantee
    is plain container isolation.
    """

    def __init__(self) -> None:
        self._engine = (os.environ.get("CONTAINER_ENGINE") or "docker").strip()
        self._runtime = (os.environ.get("SANDBOX_RUNTIME") or "").strip()
        self._python_image = (
            os.environ.get("SANDBOX_PYTHON_IMAGE") or "python:3.12-slim"
        ).strip()
        self._node_image = (
            os.environ.get("SANDBOX_NODE_IMAGE") or "node:22-slim"
        ).strip()
        if shutil.which(self._engine) is None:
            raise SandboxError(
                f"container engine {self._engine!r} not found on PATH. Install it, "
                f"set CONTAINER_ENGINE, or set SANDBOX_PROVIDER=subprocess for "
                f"development (no isolation)."
            )

        # Where run workspaces are created, and what the engine's daemon calls
        # that same directory. The two differ whenever this process is itself in
        # a container talking to the host's engine over a mounted socket: the
        # daemon resolves --volume paths in *its* filesystem, so a path that
        # exists only inside this container silently mounts as an empty
        # directory and every run fails to find its entrypoint.
        self._workspace_root = _clean_env("SANDBOX_WORKSPACE_ROOT")
        self._host_workspace_root = _clean_env("SANDBOX_HOST_WORKSPACE_ROOT")
        # Set when this process shares a filesystem with the daemon it drives —
        # true docker-in-docker — so no translation is needed and the checks
        # below would be wrong to insist on it.
        paths_already_agree = _env_bool("SANDBOX_ALLOW_UNSHARED_WORKSPACE")
        if self._host_workspace_root and not self._workspace_root:
            raise SandboxError(
                "SANDBOX_HOST_WORKSPACE_ROOT is set without SANDBOX_WORKSPACE_ROOT. "
                "Set both: the first is what the engine's daemon calls the "
                "directory, the second is where this process writes to it."
            )
        if self._workspace_root:
            Path(self._workspace_root).mkdir(parents=True, exist_ok=True)
            if not self._host_workspace_root:
                self._host_workspace_root = _discover_host_mount_source(
                    self._engine, self._workspace_root
                )
                if self._host_workspace_root:
                    logger.info(
                        "sandbox workspace %s is %s to the container engine",
                        self._workspace_root,
                        self._host_workspace_root,
                    )
                elif _in_container() and not paths_already_agree:
                    raise SandboxError(
                        f"SANDBOX_WORKSPACE_ROOT is {self._workspace_root!r}, but the "
                        f"container engine reports no mount at that path, so it "
                        f"cannot reach the code a run needs. Bind-mount a directory "
                        f"there, or set SANDBOX_HOST_WORKSPACE_ROOT to what the "
                        f"engine's daemon calls it."
                    )
        elif _in_container() and not paths_already_agree:
            raise SandboxError(
                "This process is running in a container, so a workspace directory "
                "created here is not visible to the container engine's daemon and "
                "every run would start with an empty /workspace. Bind-mount a host "
                "directory into this container and set SANDBOX_WORKSPACE_ROOT to "
                "where it lands; the host side is discovered from the engine. If "
                "this container runs its own daemon, so both already agree, set "
                "SANDBOX_ALLOW_UNSHARED_WORKSPACE=1."
            )

        # Run as the identity that owns the workspace, so what the run writes can
        # be read back and cleaned up afterwards. Left as the image's own user
        # with SANDBOX_RUN_AS_USER=off.
        self._run_as_user = _resolve_run_as_user()
        if self._runtime:
            self.isolation = (
                f"One disposable container per run on the {self._runtime!r} runtime, "
                "network disabled by default, no credentials mounted, all "
                "capabilities dropped, read-only root filesystem."
            )
        else:
            self.isolation = (
                "One disposable container per run, network disabled by default, no "
                "credentials mounted, all capabilities dropped, read-only root "
                "filesystem. Kernel is shared with the host: set SANDBOX_RUNTIME to "
                "a stronger runtime for untrusted multi-tenant workloads."
            )

    def _image_for(self, spec: SandboxSpec) -> str:
        if spec.image:
            return spec.image
        language = (spec.language or "python").strip().lower()
        return self._node_image if language in ("node", "javascript", "js") else self._python_image

    def _daemon_path(self, workdir: Path) -> str:
        """``workdir`` as the container engine's daemon will resolve it."""
        if not (self._workspace_root and self._host_workspace_root):
            return str(workdir)
        relative = workdir.relative_to(self._workspace_root)
        return str(Path(self._host_workspace_root) / relative)

    def _argv(self, spec: SandboxSpec, workdir: Path) -> list[str]:
        argv = [
            self._engine, "run", "--rm",
            "--network", "bridge" if spec.network else "none",
            "--memory", f"{max(64, int(spec.memory_mb))}m",
            "--cpus", str(max(0.1, float(spec.cpu_cores))),
            "--cap-drop", "ALL",
            "--security-opt", "no-new-privileges",
            "--read-only",
            "--tmpfs", "/tmp:rw,noexec,nosuid,size=64m",
            "--workdir", "/workspace",
            "--volume", f"{self._daemon_path(workdir)}:/workspace:rw",
        ]
        if self._run_as_user:
            argv += ["--user", self._run_as_user]
        if self._runtime:
            argv += ["--runtime", self._runtime]
        for key, value in (spec.env or {}).items():
            # Values are passed through the engine's env flag, never interpolated
            # into a shell string.
            argv += ["--env", f"{key}={value}"]
        argv.append(self._image_for(spec))
        language = (spec.language or "python").strip().lower()
        if language in ("node", "javascript", "js"):
            argv += ["node", "main.js"]
        else:
            argv += ["python", "main.py"]
        return argv

    async def run(self, code: str, *, spec: SandboxSpec | None = None) -> SandboxResult:
        spec = spec or SandboxSpec()
        loop = asyncio.get_running_loop()
        started = loop.time()
        with tempfile.TemporaryDirectory(
            prefix="sandbox-", dir=self._workspace_root or None
        ) as tmp:
            workdir = Path(tmp)
            # The run writes here as another user, so the directory has to be
            # traversable by it; the entries inside are what carry the run.
            os.chmod(workdir, 0o777)
            (workdir / _entry_filename(spec.language)).write_text(code, encoding="utf-8")
            for name, blob in (spec.files or {}).items():
                target = (workdir / name).resolve()
                if not target.is_relative_to(workdir):
                    raise SandboxError("input file path escapes the sandbox workspace")
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_bytes(blob)

            proc = await asyncio.create_subprocess_exec(
                *self._argv(spec, workdir),
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            timed_out = False
            try:
                stdout, stderr = await asyncio.wait_for(
                    proc.communicate(), timeout=max(1, int(spec.timeout_seconds))
                )
            except asyncio.TimeoutError:
                timed_out = True
                proc.kill()
                stdout, stderr = await proc.communicate()

            produced = self._collect(workdir, spec)

        return SandboxResult(
            exit_code=proc.returncode if proc.returncode is not None else -1,
            stdout=(stdout or b"").decode("utf-8", "replace"),
            stderr=(stderr or b"").decode("utf-8", "replace"),
            duration_ms=int((loop.time() - started) * 1000),
            timed_out=timed_out,
            files=produced,
        )

    @staticmethod
    def _collect(workdir: Path, spec: SandboxSpec) -> dict[str, bytes]:
        """Everything the run wrote, minus what we put there."""
        skip = {_entry_filename(spec.language), *(spec.files or {})}
        produced: dict[str, bytes] = {}
        for path in workdir.rglob("*"):
            if not path.is_file():
                continue
            name = path.relative_to(workdir).as_posix()
            if name in skip:
                continue
            try:
                produced[name] = path.read_bytes()
            except OSError:
                logger.debug("could not read produced file %s", name)
        return produced


class SubprocessSandbox(SandboxProvider):
    """No isolation. Development only.

    Runs code as a child process of the API, on the host, with the host's
    filesystem and network. Requires ``ALLOW_UNSANDBOXED_EXECUTION=1`` so that
    nobody arrives here by accident or by copying a configuration file.
    """

    isolation = (
        "NONE. Code runs as an ordinary child process with full access to this "
        "host's filesystem and network. Development use only."
    )

    def __init__(self) -> None:
        if (os.environ.get("ALLOW_UNSANDBOXED_EXECUTION") or "").strip() not in (
            "1", "true", "yes", "on",
        ):
            raise SandboxError(
                "SubprocessSandbox provides no isolation and will run "
                "model-generated code directly on this host. Set "
                "ALLOW_UNSANDBOXED_EXECUTION=1 if that is genuinely what you want."
            )
        logger.warning(
            "SubprocessSandbox active — model-generated code runs unisolated on this host"
        )

    async def run(self, code: str, *, spec: SandboxSpec | None = None) -> SandboxResult:
        spec = spec or SandboxSpec()
        loop = asyncio.get_running_loop()
        started = loop.time()
        with tempfile.TemporaryDirectory(prefix="unsandboxed-") as tmp:
            workdir = Path(tmp)
            (workdir / _entry_filename(spec.language)).write_text(code, encoding="utf-8")
            for name, blob in (spec.files or {}).items():
                target = (workdir / name).resolve()
                if not target.is_relative_to(workdir):
                    raise SandboxError("input file path escapes the workspace")
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_bytes(blob)

            env = {**os.environ, **(spec.env or {})}
            proc = await asyncio.create_subprocess_exec(
                *_language_command(spec.language),
                cwd=str(workdir),
                env=env,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            timed_out = False
            try:
                stdout, stderr = await asyncio.wait_for(
                    proc.communicate(), timeout=max(1, int(spec.timeout_seconds))
                )
            except asyncio.TimeoutError:
                timed_out = True
                proc.kill()
                stdout, stderr = await proc.communicate()
            produced = ContainerSandbox._collect(workdir, spec)

        return SandboxResult(
            exit_code=proc.returncode if proc.returncode is not None else -1,
            stdout=(stdout or b"").decode("utf-8", "replace"),
            stderr=(stderr or b"").decode("utf-8", "replace"),
            duration_ms=int((loop.time() - started) * 1000),
            timed_out=timed_out,
            files=produced,
        )
