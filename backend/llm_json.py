"""Shared LLM gateway helpers and JSON extraction from model responses.

Used by Lab classification, reply gates, and other modules that need a
consistent gateway client and robust structured-output parsing.
"""

from __future__ import annotations

import json
import logging
import os
import re
from typing import Optional

logger = logging.getLogger(__name__)

MODEL_GATEWAY_API_KEY: Optional[str] = os.environ.get("MODEL_GATEWAY_API_KEY")
_RAW_BASE = os.environ.get("MODEL_GATEWAY_URL", "http://localhost:4000").rstrip("/")
MODEL_GATEWAY_URL: str = _RAW_BASE[:-3] if _RAW_BASE.endswith("/v1") else _RAW_BASE

_JSON_FENCE_RE = re.compile(r"```(?:json)?\s*(\{.*?\}|\[.*?\])\s*```", re.DOTALL)


def build_async_client():
    import anthropic

    return anthropic.AsyncAnthropic(api_key=MODEL_GATEWAY_API_KEY, base_url=MODEL_GATEWAY_URL)


def build_sync_client():
    import anthropic

    return anthropic.Anthropic(api_key=MODEL_GATEWAY_API_KEY, base_url=MODEL_GATEWAY_URL)


def extract_json(text: str):
    """Best-effort: pull the first JSON object/array out of an LLM response."""
    if not text:
        return None
    text = text.strip()
    try:
        return json.loads(text)
    except (ValueError, TypeError):
        pass
    m = _JSON_FENCE_RE.search(text)
    if m:
        try:
            return json.loads(m.group(1))
        except (ValueError, TypeError):
            pass
    for opener, closer in (("{", "}"), ("[", "]")):
        start = text.find(opener)
        if start == -1:
            continue
        depth = 0
        for i in range(start, len(text)):
            c = text[i]
            if c == opener:
                depth += 1
            elif c == closer:
                depth -= 1
                if depth == 0:
                    try:
                        return json.loads(text[start : i + 1])
                    except (ValueError, TypeError):
                        break
    return None
