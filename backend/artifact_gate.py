"""Deterministic static gate for skill artifact Python uploads."""

from __future__ import annotations

import ast
import inspect
import json
import re
from dataclasses import dataclass, field
from typing import Any, Optional

from artifact_models import EFFECT_READ, EFFECT_WRITE

MAX_CODE_BYTES = 120_000

ALLOWED_IMPORT_ROOTS = frozenset({
    "__future__",
    "ast",
    "base64",
    "collections",
    "concurrent",
    "csv",
    "dataclasses",
    "datetime",
    "decimal",
    "docx",
    "enum",
    "functools",
    "hashlib",
    "html",
    "http",
    "io",
    "ipaddress",
    "itertools",
    "json",
    "logging",
    "math",
    "openpyxl",
    "os",
    "pathlib",
    "re",
    "requests",
    "statistics",
    "time",
    "typing",
    "urllib",
    "uuid",
    "xml",
    "zipfile",
})

BANNED_CALL_NAMES = frozenset({
    "eval",
    "exec",
    "compile",
    "__import__",
    "breakpoint",
    "globals",
    "locals",
    "system",
    "popen",
    "spawn",
    "spawnl",
    "spawnv",
    "execl",
    "execle",
    "execlp",
    "execv",
    "execve",
    "execvp",
    "urlopen",
})

BANNED_IMPORT_ROOTS = frozenset({
    "subprocess",
    "ctypes",
    "pickle",
    "marshal",
    "shelve",
    "socket",
    "pty",
    "importlib",
    "multiprocessing",
})

NAME_RE = re.compile(r"^[a-z][a-z0-9_]{2,39}$")


@dataclass
class GateViolation:
    rule: str
    line: int
    detail: str


@dataclass
class GateReport:
    ok: bool
    violations: list[GateViolation] = field(default_factory=list)
    api_summary: str = ""

    def to_json(self) -> str:
        return json.dumps(
            {
                "ok": self.ok,
                "violations": [
                    {"rule": v.rule, "line": v.line, "detail": v.detail}
                    for v in self.violations
                ],
                "api_summary": self.api_summary,
            }
        )


def review(code: str, manifest: dict) -> GateReport:
    violations: list[GateViolation] = []
    if len(code.encode("utf-8")) > MAX_CODE_BYTES:
        violations.append(GateViolation("size", 0, f"code exceeds {MAX_CODE_BYTES} bytes"))

    name = manifest.get("name", "")
    if not isinstance(name, str) or not NAME_RE.match(name):
        violations.append(GateViolation("name", 0, "invalid artifact name"))

    effect = manifest.get("effect_class", EFFECT_READ)
    if effect not in (EFFECT_READ, EFFECT_WRITE):
        violations.append(GateViolation("effect_class", 0, "must be read or write"))

    entrypoint = manifest.get("entrypoint", "")
    if not isinstance(entrypoint, str) or not entrypoint.isidentifier():
        violations.append(GateViolation("entrypoint", 0, "entrypoint must be a valid identifier"))

    try:
        tree = ast.parse(code)
    except SyntaxError as exc:
        violations.append(
            GateViolation("syntax", exc.lineno or 0, exc.msg or "syntax error")
        )
        return GateReport(ok=False, violations=violations)

    _walk(tree, violations)
    _check_imports(tree, violations)

    fn_node = _find_function(tree, entrypoint) if isinstance(entrypoint, str) else None
    if entrypoint and fn_node is None:
        violations.append(
            GateViolation("entrypoint", 0, f"function {entrypoint!r} not found at module level")
        )

    api_summary = ""
    if fn_node is not None:
        api_summary = _summarize_function(fn_node, manifest.get("params_schema"))

    if not violations and fn_node is not None:
        _check_params_schema(fn_node, manifest.get("params_schema"), violations)

    return GateReport(ok=not violations, violations=violations, api_summary=api_summary)


def _walk(node: ast.AST, violations: list[GateViolation]) -> None:
    for child in ast.walk(node):
        if isinstance(child, ast.Call):
            func = child.func
            if isinstance(func, ast.Name) and func.id in BANNED_CALL_NAMES:
                violations.append(
                    GateViolation("banned_call", child.lineno, func.id)
                )
            if isinstance(func, ast.Attribute) and func.attr in BANNED_CALL_NAMES - {"compile"}:
                violations.append(
                    GateViolation("banned_call", child.lineno, func.attr)
                )


def _check_imports(tree: ast.Module, violations: list[GateViolation]) -> None:
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                root = (alias.name or "").split(".")[0]
                if root in BANNED_IMPORT_ROOTS:
                    violations.append(
                        GateViolation("banned_import", node.lineno, root)
                    )
                elif root and root not in ALLOWED_IMPORT_ROOTS:
                    violations.append(
                        GateViolation("import_allowlist", node.lineno, root)
                    )
        elif isinstance(node, ast.ImportFrom):
            root = (node.module or "").split(".")[0]
            if root in BANNED_IMPORT_ROOTS:
                violations.append(
                    GateViolation("banned_import", node.lineno, root or "<relative>")
                )
            elif root and root not in ALLOWED_IMPORT_ROOTS:
                violations.append(
                    GateViolation("import_allowlist", node.lineno, root)
                )


def _find_function(tree: ast.Module, name: str) -> Optional[ast.FunctionDef]:
    for node in tree.body:
        if isinstance(node, ast.FunctionDef) and node.name == name:
            return node
    return None


def _summarize_function(fn: ast.FunctionDef, params_schema: Any) -> str:
    args = [a.arg for a in fn.args.args if a.arg != "self"]
    doc = ast.get_docstring(fn) or ""
    doc_line = doc.strip().splitlines()[0] if doc.strip() else ""
    schema_hint = ""
    if isinstance(params_schema, dict):
        required = params_schema.get("required") or []
        if required:
            schema_hint = f" required={required}"
    return f"{fn.name}({', '.join(args)}){schema_hint} — {doc_line}".strip()


def _check_params_schema(
    fn: ast.FunctionDef,
    params_schema: Any,
    violations: list[GateViolation],
) -> None:
    if not isinstance(params_schema, dict):
        return
    required = params_schema.get("required") or []
    if not isinstance(required, list):
        return
    fn_args = {a.arg for a in fn.args.args}
    for key in required:
        if isinstance(key, str) and key not in fn_args:
            violations.append(
                GateViolation(
                    "params_schema",
                    fn.lineno,
                    f"required param {key!r} missing from signature",
                )
            )
