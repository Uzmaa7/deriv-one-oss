"""Static checks for Python executed in workspace/agent sandboxes."""

from __future__ import annotations

import ast
import re

_BLOCKED_IMPORT_ROOTS = frozenset({"subprocess", "pty", "pwd", "grp"})
_BLOCKED_OS_ATTRS = frozenset(
    {
        "system",
        "popen",
        "spawnv",
        "spawnl",
        "spawnve",
        "execl",
        "execle",
        "execlp",
        "execv",
        "execve",
        "execvp",
    }
)
_BLOCKED_CALL_NAMES = frozenset({"eval", "exec", "compile", "__import__"})

# Fallback for non-parseable snippets
_BLOCKED_PATTERN = re.compile(
    r"(?:\bimport\s+pwd\b|\bfrom\s+pwd\b|\bos\.(?:system|popen|spawnv|spawnl|execv)\b|"
    r"\bsubprocess\.|urllib\.request\.urlopen)",
    re.IGNORECASE,
)


def validate_sandbox_python(code: str) -> str | None:
    """Return an error message when *code* uses disallowed sandbox capabilities."""
    if not (code or "").strip():
        return None
    try:
        tree = ast.parse(code)
    except SyntaxError:
        if _BLOCKED_PATTERN.search(code):
            return "This code uses sandbox capabilities that are not permitted."
        return None

    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                root = (alias.name or "").split(".")[0]
                if root in _BLOCKED_IMPORT_ROOTS:
                    return f"Import of '{root}' is not permitted in the sandbox."
        elif isinstance(node, ast.ImportFrom):
            root = (node.module or "").split(".")[0]
            if root in _BLOCKED_IMPORT_ROOTS:
                return f"Import of '{root}' is not permitted in the sandbox."
        elif isinstance(node, ast.Call):
            func = node.func
            if isinstance(func, ast.Name) and func.id in _BLOCKED_CALL_NAMES:
                return f"Call to '{func.id}' is not permitted in the sandbox."
            if isinstance(func, ast.Attribute):
                if func.attr in _BLOCKED_CALL_NAMES or func.attr in _BLOCKED_OS_ATTRS:
                    return f"Call to '{func.attr}' is not permitted in the sandbox."
                if func.attr == "urlopen" and isinstance(func.value, ast.Attribute):
                    if getattr(func.value, "attr", "") == "request":
                        return "Network urlopen is not permitted in the sandbox."
    return None
