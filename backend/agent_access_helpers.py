"""Shared agent ownership helpers."""

from __future__ import annotations


def emails_match(a: str | None, b: str | None) -> bool:
    """True only when both emails are non-empty and equal (case-insensitive)."""
    left = (a or "").strip().lower()
    right = (b or "").strip().lower()
    return bool(left and right and left == right)


def creator_email_matches(creator_email: str | None, user_email: str | None) -> bool:
    return emails_match(creator_email, user_email)


def is_agent_owner(agent, owner_sub: str, user_email: str) -> bool:
    return agent.owner_sub == owner_sub or creator_email_matches(
        getattr(agent, "creator_email", None),
        user_email,
    )
