"""Search a user's Workspace chat history by title and message content.

Kept out of ``main.py`` so it can be unit-tested against an in-memory SQLite
database without importing the whole app.

``LIKE`` over ``func.lower(...)`` rather than ``ILIKE``: the dev database is
SQLite (``hub.db``) and production is Postgres, and lowercase-plus-LIKE is the
only form that behaves identically on both.
"""

from __future__ import annotations

from typing import Any

from sqlalchemy import func, or_

from database import Chat, Message

# Below this, a query matches so much that the result list is noise.
MIN_QUERY_CHARS = 2
# Hard ceiling on rows handed back regardless of what the caller asks for.
MAX_LIMIT = 50
# Rows pulled from the join before grouping. Generous enough that a user with a
# few hundred chats still gets complete match counts, bounded enough that a
# one-letter-ish query can't stream the whole table into memory.
MAX_SCANNED_ROWS = 2000
# Characters of context kept on each side of the matched term in a snippet.
SNIPPET_RADIUS = 60


def _like_pattern(needle: str) -> str:
    """Escape LIKE metacharacters so a query for "50%" means literally "50%"."""
    escaped = needle.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
    return f"%{escaped}%"


def _snippet(content: str, needle: str) -> str:
    """A window of *content* around the first occurrence of *needle*."""
    # find() returns -1 when the needle is not in this message; fall back to the
    # start of the text rather than indexing from the end.
    idx = max(content.lower().find(needle), 0)
    start = max(0, idx - SNIPPET_RADIUS)
    end = min(len(content), idx + len(needle) + SNIPPET_RADIUS)
    text = content[start:end].strip()
    if start > 0:
        text = f"…{text}"
    if end < len(content):
        text = f"{text}…"
    return text


def search_chats(
    db: Any,
    owner_sub: str,
    query: str,
    limit: int = 25,
) -> list[dict]:
    """Return the caller's chats whose title or messages contain *query*.

    Tenancy is enforced inside the query on ``Chat.owner_sub`` — a caller can
    never see another user's chats, matched or not.
    """
    needle = (query or "").strip().lower()
    if len(needle) < MIN_QUERY_CHARS or not owner_sub:
        return []

    limit = max(1, min(int(limit or 1), MAX_LIMIT))
    pattern = _like_pattern(needle)

    rows = (
        db.query(
            Chat.id,
            Chat.title,
            Chat.active_skill_id,
            Chat.updated_at,
            Message.content,
            Message.created_at,
        )
        .outerjoin(Message, Message.chat_id == Chat.id)
        .filter(Chat.owner_sub == owner_sub)
        .filter(
            or_(
                func.lower(Chat.title).like(pattern, escape="\\"),
                func.lower(Message.content).like(pattern, escape="\\"),
            )
        )
        .order_by(Chat.updated_at.desc(), Message.created_at.asc())
        .limit(MAX_SCANNED_ROWS)
        .all()
    )

    # Group per chat, preserving the updated_at-desc order the query produced.
    grouped: dict[str, dict] = {}
    for chat_id, title, skill_id, updated_at, content, _created_at in rows:
        entry = grouped.get(chat_id)
        if entry is None:
            entry = {
                "id": chat_id,
                "title": title,
                "active_skill_id": skill_id,
                "updated_at": updated_at.isoformat() if updated_at else None,
                "match_count": 0,
                "title_match": needle in (title or "").lower(),
                "snippet": None,
            }
            grouped[chat_id] = entry
        # A title-only match still produces one row, with content None.
        if content and needle in content.lower():
            entry["match_count"] += 1
            if entry["snippet"] is None:
                entry["snippet"] = _snippet(content, needle)

    out: list[dict] = []
    for entry in list(grouped.values())[:limit]:
        title_match = entry.pop("title_match")
        body_matches = entry["match_count"] > 0
        entry["matched_in"] = (
            "both" if title_match and body_matches
            else "title" if title_match
            else "message"
        )
        out.append(entry)
    return out
