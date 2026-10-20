"""Reap sandboxes leaked by a caller that died mid-run.

A sandbox is normally destroyed by the code that created it. That does not
happen when the process crashes, the task is cancelled mid-flight, or teardown
itself fails — and a leaked sandbox holds resources indefinitely.

The reaping itself is provider-specific: only the provider knows what a
sandbox is and how to tell an abandoned one from a running one. So this module
is a thin scheduler-facing wrapper over
:meth:`~providers.sandbox.SandboxProvider.reap`.

Providers that cannot leak — a subprocess dies with its parent — return 0, and
the periodic sweep costs nothing.
"""

from __future__ import annotations

import asyncio
import logging

from providers import get_sandbox

logger = logging.getLogger(__name__)


async def reap_stale_sandbox_claims(
    max_age_seconds: int | None = None,
    namespace: str | None = None,
) -> int:
    """Destroy abandoned sandboxes. Returns how many were removed.

    ``max_age_seconds`` and ``namespace`` are hints. Whether either is
    meaningful is the provider's business — how old is "abandoned", and whether
    sandboxes are grouped at all, are properties of the implementation. They are
    accepted so callers that know their deployment can express intent, and
    ignored by providers for which they mean nothing.

    Never raises: this runs on a timer, and a reaping failure must not take down
    the scheduler that called it.
    """
    try:
        sandbox = get_sandbox()
        try:
            removed = await sandbox.reap(max_age_seconds=max_age_seconds, namespace=namespace)
        except TypeError:
            # Provider takes no hints — the common case.
            removed = await sandbox.reap()
    except Exception:
        logger.exception("sandbox_reaper: reap failed")
        return 0
    if removed:
        logger.info("sandbox_reaper: reaped %d abandoned sandbox(es)", removed)
    return removed


def reap_stale_sandbox_claims_sync(
    max_age_seconds: int | None = None,
    namespace: str | None = None,
) -> int:
    """Blocking wrapper, for schedulers that are not running an event loop."""
    try:
        return asyncio.run(reap_stale_sandbox_claims(max_age_seconds, namespace))
    except RuntimeError:
        # Already inside a running loop — the async form is the right call there.
        logger.warning(
            "sandbox_reaper: called the sync reaper from inside an event loop; "
            "use reap_stale_sandbox_claims() instead"
        )
        return 0
    except Exception:
        logger.exception("sandbox_reaper: reap failed")
        return 0
