"""Fingerprints for run_python code — exact and structural (shape) hashes."""

from __future__ import annotations

import ast
import hashlib
import re
import tokenize
from io import BytesIO
from typing import Optional


def code_sha256(code: str) -> str:
    return hashlib.sha256(code.encode("utf-8")).hexdigest()


def shape_sha256(code: str) -> Optional[str]:
    """Normalize identifiers and literals, then hash. Returns None if code is not valid Python."""
    try:
        normalized = _normalize_python_shape(code)
    except (SyntaxError, tokenize.TokenError, ValueError):
        return None
    return hashlib.sha256(normalized.encode("utf-8")).hexdigest()


def _normalize_python_shape(code: str) -> str:
    tree = ast.parse(code)
    parts: list[str] = []

    def visit(node: ast.AST) -> None:
        if isinstance(node, ast.Name):
            parts.append("id")
        elif isinstance(node, ast.Constant):
            parts.append(type(node.value).__name__)
        elif isinstance(node, ast.JoinedStr):
            parts.append("fstr")
        else:
            parts.append(type(node).__name__)
        for child in ast.iter_child_nodes(node):
            visit(child)

    visit(tree)
    return "|".join(parts)
