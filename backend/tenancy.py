"""Row-level tenancy: derive the owner id from the token's `sub` claim.

The subject is the only claim tenancy depends on, so changing identity provider
does not change who can see what.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Type, TypeVar

from database import OWNER_LEGACY_SENTINEL
from fastapi import HTTPException, Request

_T = TypeVar("_T")


def request_owner_sub(request: Request) -> str:
    auth = getattr(request.state, "auth", None)
    sub = getattr(auth, "sub", None) if auth else None
    if not isinstance(sub, str) or not sub.strip():
        raise HTTPException(status_code=401, detail="Not authenticated")
    return sub.strip()


def get_tenant_or_404(
    db: Any,
    model: Type[_T],
    pk: Any,
    owner_sub: str,
    detail: str = "Not found",
) -> _T:
    """Fetch a row by primary key and verify it belongs to owner_sub.

    Raises HTTP 404 (not found) if the row does not exist OR if its
    ``owner_sub`` does not match the caller's identity.  The identical
    response prevents tenant enumeration — callers cannot tell the
    difference between "missing" and "owned by someone else".
    """
    row = db.get(model, pk)
    if row is None or getattr(row, "owner_sub", None) != owner_sub:
        raise HTTPException(status_code=404, detail=detail)
    return row


def workspace_subdirectory_for_owner(base_generated: Path, owner_sub: str) -> Path:
    """Sandbox directory for uploads / subprocess PDFs — derived from hashed sub."""
    import hashlib

    slug = hashlib.sha256(owner_sub.encode("utf-8")).hexdigest()[:28]
    d = base_generated / f"w_{slug}"
    d.mkdir(parents=True, exist_ok=True)
    return d.resolve()


__all__ = [
    "OWNER_LEGACY_SENTINEL",
    "get_tenant_or_404",
    "request_owner_sub",
    "workspace_subdirectory_for_owner",
]
