"""Writable paths inside gVisor executor sandboxes.

Sandbox pods expose ``/tmp`` as the only writable root. These constants are
embedded into generated prolog/epilog code — they are not host ``tempfile`` paths.
"""

from __future__ import annotations

# Bandit B108: literal /tmp is required inside sandbox pods, not host tempfile.
SANDBOX_TMP_ROOT = "/tmp"  # nosec B108


def sandbox_path(*parts: str) -> str:
    """Join path segments under the sandbox writable root."""
    return "/".join((SANDBOX_TMP_ROOT, *parts))
