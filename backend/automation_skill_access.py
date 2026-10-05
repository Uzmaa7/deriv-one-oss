"""Skill ACL enforcement for automations."""

from __future__ import annotations

import asyncio
from typing import Optional

from fastapi import HTTPException


async def accessible_skill_slugs(user_sub: str, user_email: Optional[str]) -> frozenset[str]:
    from skill_router import resolve_user_skill_access

    _, _, slugs = await resolve_user_skill_access(user_sub, user_email)
    return slugs


def user_can_access_skill(
    user_sub: str,
    user_email: Optional[str],
    skill_id: Optional[str],
) -> bool:
    if not skill_id:
        return True
    try:
        slugs = asyncio.run(accessible_skill_slugs(user_sub, user_email))
        return skill_id in slugs
    except Exception:
        return False


def ensure_skill_granted(
    user_sub: str,
    user_email: Optional[str],
    skill_id: Optional[str],
) -> None:
    """Raise HTTP 403 when the user may not use *skill_id* in an automation."""
    if not skill_id:
        return
    if not user_can_access_skill(user_sub, user_email, skill_id):
        raise HTTPException(
            status_code=403,
            detail=f"You do not have access to skill '{skill_id}'.",
        )
