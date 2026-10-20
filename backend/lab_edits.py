"""Helpers to summarize Lab file edits for chat / timeline."""

from __future__ import annotations

import difflib
from typing import Any


_MAX_PREVIEW_LINES = 16
_MAX_LINE_LEN = 200


def _clip_line(line: str) -> str:
    s = line.rstrip("\n\r")
    if len(s) > _MAX_LINE_LEN:
        return s[: _MAX_LINE_LEN - 1] + "…"
    return s


def build_file_edit_summary(
    path: str,
    *,
    kind: str,
    old_text: str = "",
    new_text: str = "",
) -> dict[str, Any]:
    """Build a compact, UI-friendly edit summary for one file."""
    old_lines = old_text.splitlines()
    new_lines = new_text.splitlines()
    additions = 0
    deletions = 0
    preview: list[dict[str, str]] = []

    for tag, _i1, _i2, _j1, _j2 in difflib.SequenceMatcher(
        None, old_lines, new_lines
    ).get_opcodes():
        if tag == "equal":
            continue
        if tag in ("replace", "delete"):
            for line in old_lines[_i1:_i2]:
                deletions += 1
                if len(preview) < _MAX_PREVIEW_LINES:
                    preview.append({"type": "del", "text": _clip_line(line)})
        if tag in ("replace", "insert"):
            for line in new_lines[_j1:_j2]:
                additions += 1
                if len(preview) < _MAX_PREVIEW_LINES:
                    preview.append({"type": "add", "text": _clip_line(line)})

    if kind == "write" and not old_text and additions == 0 and new_text:
        for line in new_lines[:_MAX_PREVIEW_LINES]:
            additions += 1
            preview.append({"type": "add", "text": _clip_line(line)})

    return {
        "path": path,
        "kind": kind,
        "additions": additions,
        "deletions": deletions,
        "preview": preview,
    }
