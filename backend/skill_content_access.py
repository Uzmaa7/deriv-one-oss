"""Authorization for reading published skill source (SKILL.md)."""

from __future__ import annotations

from fastapi import HTTPException

from skill_router import resolve_user_skill_access


async def assert_skill_content_readable(
    user_sub: str,
    user_email: str | None,
    slug: str,
    *,
    is_admin: bool = False,
) -> None:
    """Raise 404 when the caller may not read SKILL.md for *slug*."""
    slug_norm = slug.strip().lower()
    results, _, accessible = await resolve_user_skill_access(user_sub, user_email)
    if slug_norm in accessible:
        return

    row = next((r for r in results if r.slug == slug_norm), None)
    if row is not None and not row.accessible and not is_admin:
        raise HTTPException(status_code=404, detail="Skill content not found.")

    if is_admin:
        return

    raise HTTPException(status_code=404, detail="Skill content not found.")
