"""Bounded pool for cron-fired subprocess workers (agent tasks, automations).

Each spawn loads a full Python stack + LLM + sandbox and can use ~700–900 MiB.
Child processes share the scheduler pod memory cgroup, so concurrency must be
capped and paired with a proportional memory limit (see scheduler deployment).

Usage (from scheduler_worker after patching spawn helpers):
    pool.run(target=run_agent_scheduled_task, args=(agent_id, task_id), label=...)
"""

from __future__ import annotations

import logging
import multiprocessing
import os
import threading
from collections.abc import Callable
from typing import Any

logger = logging.getLogger(__name__)

DEFAULT_CRON_MAX_WORKERS = 2
DEFAULT_MAINTENANCE_MAX_WORKERS = 4

CRON_EXECUTOR = "cron"
DEFAULT_EXECUTOR = "default"


def cron_max_workers() -> int:
    raw = os.environ.get("SCHEDULER_CRON_MAX_WORKERS", str(DEFAULT_CRON_MAX_WORKERS))
    try:
        return max(1, int(raw))
    except ValueError:
        return DEFAULT_CRON_MAX_WORKERS


def maintenance_max_workers() -> int:
    raw = os.environ.get(
        "SCHEDULER_MAINTENANCE_MAX_WORKERS",
        str(DEFAULT_MAINTENANCE_MAX_WORKERS),
    )
    try:
        return max(1, int(raw))
    except ValueError:
        return DEFAULT_MAINTENANCE_MAX_WORKERS


_pool: "CronWorkerPool | None" = None
_pool_lock = threading.Lock()


class CronWorkerPool:
    """Limit how many heavy cron subprocesses run at once inside the scheduler pod."""

    def __init__(self, max_workers: int) -> None:
        self._max_workers = max_workers
        self._semaphore = threading.Semaphore(max_workers)
        self._active_lock = threading.Lock()
        self._active = 0

    @property
    def max_workers(self) -> int:
        return self._max_workers

    @property
    def active_workers(self) -> int:
        with self._active_lock:
            return self._active

    def run(
        self,
        *,
        target: Callable[..., Any],
        args: tuple[Any, ...] = (),
        label: str,
        timeout: int,
        on_timeout: Callable[[], None] | None = None,
        on_error_exit: Callable[[int], None] | None = None,
    ) -> None:
        """Spawn *target* in an isolated process; block until it finishes or times out."""
        logger.info(
            "cron_worker_pool: waiting for slot (%s active/%s max) label=%s",
            self.active_workers,
            self._max_workers,
            label,
        )
        self._semaphore.acquire()
        with self._active_lock:
            self._active += 1
        logger.info(
            "cron_worker_pool: started slot (%s active/%s max) label=%s",
            self.active_workers,
            self._max_workers,
            label,
        )

        ctx = multiprocessing.get_context("spawn")
        proc = ctx.Process(target=target, args=args, daemon=True)
        proc.start()
        proc.join(timeout=timeout)

        try:
            if proc.is_alive():
                logger.warning(
                    "cron_worker_pool: timed out after %ds label=%s — terminating",
                    timeout,
                    label,
                )
                if on_timeout:
                    try:
                        on_timeout()
                    except Exception:
                        logger.exception("cron_worker_pool: on_timeout failed label=%s", label)
                proc.terminate()
                proc.join(timeout=5)
                if proc.is_alive():
                    proc.kill()
                return

            if proc.exitcode != 0:
                logger.warning(
                    "cron_worker_pool: exited code %d label=%s",
                    proc.exitcode,
                    label,
                )
                if on_error_exit:
                    try:
                        on_error_exit(proc.exitcode or 1)
                    except Exception:
                        logger.exception("cron_worker_pool: on_error_exit failed label=%s", label)
        finally:
            with self._active_lock:
                self._active -= 1
            self._semaphore.release()


def get_cron_worker_pool() -> CronWorkerPool:
    global _pool
    if _pool is not None:
        return _pool
    with _pool_lock:
        if _pool is None:
            workers = cron_max_workers()
            _pool = CronWorkerPool(workers)
            logger.info("cron_worker_pool: initialized max_workers=%s", workers)
        return _pool
