"""Redact secrets and PII before persisting generated code samples."""

from __future__ import annotations

import re

# Secrets only — operational identifiers (email, UUID, loginid, Slack IDs,
# account numbers) are left intact so agents can act on them.
_REDACTION_RULES: list[tuple[re.Pattern[str], str]] = [
    (
        re.compile(r"(?i)(password|secret|token|api[_\-]?key|private[_\-]?key)\s*[:=]\s*\S+"),
        "[REDACTED-SECRET]",
    ),
    (re.compile(r"postgresql(\+asyncpg)?://[^\s\"'<>]+"), "[REDACTED-DB-URL]"),
    (re.compile(r"(xoxb|xoxp|xapp)-[A-Za-z0-9\-]{10,}"), "[REDACTED-SLACK-TOKEN]"),
    (re.compile(r"sk-[A-Za-z0-9]{20,}"), "[REDACTED-API-KEY]"),
    (re.compile(r"AIza[0-9A-Za-z\-_]{35}"), "[REDACTED-API-KEY]"),
    (re.compile(r"AKIA[0-9A-Z]{16}"), "[REDACTED-AWS-KEY]"),
    (re.compile(r"eyJ[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+"), "[REDACTED-JWT]"),
]


def redact_text(text: str) -> str:
    out = text
    for pattern, replacement in _REDACTION_RULES:
        out = pattern.sub(replacement, out)
    return out
