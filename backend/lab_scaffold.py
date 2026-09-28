"""Seed starter scaffolds for new Lab apps."""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Callable

logger = logging.getLogger(__name__)

SCAFFOLDS_ROOT = Path(__file__).parent / "lab_scaffolds"


def _substitute(content: str, *, app_id: str, app_name: str) -> str:
    return (
        content.replace("{{APP_ID}}", app_id)
        .replace("{{APP_NAME}}", app_name)
        .replace("__APP_NAME__", app_name)
    )


def seed_scaffold(
    app_id: str,
    tech_stack: str,
    app_name: str,
    write_fn: Callable[[str, str], None],
) -> list[str]:
    """Copy scaffold files into the app. Returns list of relative paths written."""
    if tech_stack == "react":
        scaffold_dir = SCAFFOLDS_ROOT / "react"
    else:
        scaffold_dir = SCAFFOLDS_ROOT / "vanilla"

    if not scaffold_dir.is_dir():
        logger.warning("lab_scaffold: missing scaffold dir %s", scaffold_dir)
        return []

    written: list[str] = []
    for src in sorted(scaffold_dir.rglob("*")):
        if not src.is_file():
            continue
        rel = str(src.relative_to(scaffold_dir)).replace("\\", "/")
        try:
            raw = src.read_text(encoding="utf-8")
            content = _substitute(raw, app_id=app_id, app_name=app_name)
            write_fn(rel, content)
            written.append(rel)
        except OSError:
            logger.exception("lab_scaffold: failed to seed %s for app %s", rel, app_id)
    return written
