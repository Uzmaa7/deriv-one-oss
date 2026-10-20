"""In-process cancellation controls for active workspace chat streams."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from typing import Literal

CancelMode = Literal["stop", "answer_now"]


@dataclass
class ChatSessionControl:
    event: asyncio.Event = field(default_factory=asyncio.Event)
    mode: CancelMode = "stop"


_sessions: dict[str, ChatSessionControl] = {}


def session_key(owner_sub: str, chat_id: str) -> str:
    return f"{owner_sub}:{chat_id}"


def register_session(owner_sub: str, chat_id: str) -> ChatSessionControl:
    key = session_key(owner_sub, chat_id)
    ctrl = ChatSessionControl()
    _sessions[key] = ctrl
    return ctrl


def get_session(owner_sub: str, chat_id: str) -> ChatSessionControl | None:
    return _sessions.get(session_key(owner_sub, chat_id))


def clear_session(owner_sub: str, chat_id: str) -> None:
    _sessions.pop(session_key(owner_sub, chat_id), None)


def request_cancel(owner_sub: str, chat_id: str, mode: CancelMode) -> bool:
    ctrl = get_session(owner_sub, chat_id)
    if ctrl is None:
        return False
    ctrl.mode = mode
    ctrl.event.set()
    return True
