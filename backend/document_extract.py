"""Shared limits for document text extraction."""

from __future__ import annotations

import os

MAX_EXTRACT_CHARS = int(os.environ.get("KB_MAX_EXTRACT_CHARS", str(2_000_000)))
MAX_DOCX_PARTS = int(os.environ.get("KB_MAX_DOCX_PARTS", "50000"))


def cap_extracted_text(text: str) -> str:
    if len(text) <= MAX_EXTRACT_CHARS:
        return text
    return text[:MAX_EXTRACT_CHARS] + "\n\n[Text extraction truncated]"
