"""Durable persistence for the skill catalogue.

Skills are edited as files on local disk — that is what the publish pipeline,
the router and the artifact gate all read. Local disk is not durable across
container restarts, so the catalogue is mirrored into the configured
:class:`~providers.blobs.BlobStore`:

    skills-acl.yaml            the access manifest
    skills/<slug>/skill.md     one file per skill

On startup the store is pulled down to disk; on every publish, edit or delete
the change is pushed back. The layout mirrors the local directory deliberately,
so somebody inspecting the store sees the same shape they would on disk.

Every operation here is best-effort and logs rather than raises. A storage
outage must not stop the application from starting or a user from publishing —
the local copy is still authoritative for the running process.
"""

from __future__ import annotations

import logging
import os
from pathlib import Path

from providers import get_blob_store

logger = logging.getLogger(__name__)

MANIFEST_KEY = "skills-acl.yaml"
SKILLS_PREFIX = "skills/"


def _enabled() -> bool:
    """Whether to mirror at all.

    Set ``SKILLS_STORE_ENABLED=0`` for a single-node deployment whose disk is
    already durable — the mirror is then pure overhead.
    """
    return (os.environ.get("SKILLS_STORE_ENABLED", "1").strip().lower()
            not in ("0", "false", "no", "off"))


def _local_manifest_path() -> Path:
    manifest_env = os.environ.get("SKILLS_ACL_MANIFEST", "").strip()
    if manifest_env:
        return Path(manifest_env)
    from skills_acl import _MANIFEST_PATH

    return Path(_MANIFEST_PATH)


def _local_skills_dir() -> Path:
    return _local_manifest_path().parent / "skills"


# ── Startup: pull ─────────────────────────────────────────────────────────────

def pull_from_store() -> None:
    """Copy the manifest and every skill file from the store onto local disk.

    Called once at startup, and safe to call again. Anything already on disk is
    overwritten by the stored copy, which is the durable one.
    """
    if not _enabled():
        logger.info("skills_store: mirroring disabled — skipping pull")
        return

    try:
        store = get_blob_store()
    except Exception:
        logger.exception("skills_store: no blob store available — skipping pull")
        return

    manifest_path = _local_manifest_path()
    try:
        if store.exists(MANIFEST_KEY):
            manifest_path.parent.mkdir(parents=True, exist_ok=True)
            remote = store.get(MANIFEST_KEY)
            if manifest_path.exists() and manifest_path.read_bytes() == remote:
                logger.debug("skills_store: manifest unchanged — skip write")
            else:
                manifest_path.write_bytes(remote)
                logger.info("skills_store: pulled %s", MANIFEST_KEY)
        else:
            logger.info("skills_store: no stored manifest — using the local seed")
    except Exception:
        logger.exception("skills_store: error pulling the manifest")

    skills_dir = _local_skills_dir()
    try:
        blobs = store.list(SKILLS_PREFIX)
        for info in blobs:
            relative = info.key[len(SKILLS_PREFIX):]  # e.g. my-skill/skill.md
            if not relative:
                continue
            dest = skills_dir / relative
            try:
                dest.parent.mkdir(parents=True, exist_ok=True)
                remote = store.get(info.key)
                if dest.exists() and dest.read_bytes() == remote:
                    continue
                dest.write_bytes(remote)
            except Exception:
                logger.exception("skills_store: error pulling %s", info.key)
        logger.info("skills_store: pulled %d skill file(s)", len(blobs))
    except Exception:
        logger.exception("skills_store: error listing stored skills")

    try:
        from skills_acl import reload_manifest

        reload_manifest()
    except Exception:
        logger.exception("skills_store: error reloading the manifest after pull")


# ── Publish: push ─────────────────────────────────────────────────────────────

def push_manifest() -> None:
    """Upload the current manifest. Called after every publish, edit or delete."""
    if not _enabled():
        return
    manifest_path = _local_manifest_path()
    if not manifest_path.exists():
        logger.warning("skills_store: manifest %s not found — cannot push", manifest_path)
        return
    try:
        get_blob_store().put(
            MANIFEST_KEY, manifest_path.read_bytes(), content_type="application/yaml"
        )
        logger.info("skills_store: pushed %s", MANIFEST_KEY)
    except Exception:
        logger.exception("skills_store: error pushing the manifest")


def push_skill_file(slug: str, skill_file_path: Path) -> None:
    """Upload one skill file. Called after publish or edit."""
    if not _enabled():
        return
    if not skill_file_path.exists():
        logger.warning("skills_store: %s not found — cannot push", skill_file_path)
        return
    key = f"{SKILLS_PREFIX}{slug}/skill.md"
    try:
        get_blob_store().put(
            key, skill_file_path.read_bytes(), content_type="text/markdown; charset=utf-8"
        )
        logger.info("skills_store: pushed %s", key)
    except Exception:
        logger.exception("skills_store: error pushing the skill file for '%s'", slug)


def delete_skill(slug: str) -> None:
    """Remove a skill's stored files. Called after deletion."""
    if not _enabled():
        return
    try:
        removed = get_blob_store().delete_prefix(f"{SKILLS_PREFIX}{slug}/")
        logger.info("skills_store: deleted %d stored file(s) for '%s'", removed, slug)
    except Exception:
        logger.exception("skills_store: error deleting stored files for '%s'", slug)
