"""Shared serialization of Anthropic assistant content blocks for replay.

Every agent loop that sends the model's own response back on the next turn (the
tool-calling round) has to re-serialize its content blocks into plain dicts.
Two rules matter, and getting either wrong breaks the next request:

1. ``thinking`` blocks MUST keep their ``signature``. Anthropic rejects a
   replayed thinking block that lost it::

       messages.N.content.0.thinking.signature: Field required

   ``redacted_thinking`` blocks must likewise keep their opaque ``data``.

2. SDK-only extras (``toolset_name``, ``cache_control=None``) must be dropped.
   ``model_dump()`` includes them and the API answers "Extra inputs are not
   permitted", so we rebuild each block field by field instead.

This applies to *every* loop, not only the ones that pass ``thinking=`` — Claude
Sonnet 5 emits signed thinking blocks on tool-calling turns even when extended
thinking was never requested.
"""

from __future__ import annotations

from typing import Any


def serialize_assistant_block(block: Any) -> dict:
    """Serialize one assistant content block into an API-safe dict."""
    block_type = getattr(block, "type", "")

    if block_type == "text":
        return {"type": "text", "text": str(getattr(block, "text", "") or "")}

    if block_type == "thinking":
        serialized: dict[str, Any] = {
            "type": "thinking",
            "thinking": str(getattr(block, "thinking", "") or ""),
        }
        signature = getattr(block, "signature", None)
        if signature:
            serialized["signature"] = str(signature)
        return serialized

    if block_type == "redacted_thinking":
        # Opaque, encrypted payload — pass it back byte-for-byte.
        return {
            "type": "redacted_thinking",
            "data": str(getattr(block, "data", "") or ""),
        }

    if block_type == "tool_use":
        return {
            "type": "tool_use",
            "id": str(getattr(block, "id", "") or ""),
            "name": str(getattr(block, "name", "") or ""),
            "input": block.input if isinstance(getattr(block, "input", None), dict) else {},
        }

    if block_type == "server_tool_use":
        # Produced by web_search / web_fetch; handled server-side, passed back as-is.
        return {
            "type": "server_tool_use",
            "id": str(getattr(block, "id", "") or ""),
            "name": str(getattr(block, "name", "") or ""),
            "input": block.input if isinstance(getattr(block, "input", None), dict) else {},
        }

    # Unknown block type — degrade to a text representation rather than dropping
    # it, so the turn still carries something coherent.
    return {"type": "text", "text": str(block)}


def serialize_assistant_content(blocks: Any) -> list[dict]:
    """Serialize a full assistant ``content`` list for replay on the next turn."""
    return [serialize_assistant_block(b) for b in blocks]
