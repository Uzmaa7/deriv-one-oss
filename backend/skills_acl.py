"""Skills ACL — load the manifest and resolve per-user skill access.

The manifest lives at  src/data/skills-acl.yaml  (relative to the repo root).
Loaded once at import time; call reload_manifest() to refresh after edits.
"""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal, Optional

import yaml

logger = logging.getLogger(__name__)

# ── Manifest loading ──────────────────────────────────────────────────────────

def _default_manifest_path() -> Path:
    # In Docker the file is COPY-ed to /src/data/skills-acl.yaml.
    # In dev/test it lives at <repo-root>/src/data/skills-acl.yaml.
    docker_path = Path("/src/data/skills-acl.yaml")
    if docker_path.exists():
        return docker_path
    # Walk up from this file (/app/skills_acl.py or backend/skills_acl.py)
    # to find the repo root's src/data/ directory.
    here = Path(__file__).resolve().parent
    for candidate in [here, here.parent, here.parent.parent]:
        p = candidate / "src" / "data" / "skills-acl.yaml"
        if p.exists():
            return p
    # Final fallback — will produce a clear FileNotFoundError later
    return here.parent / "src" / "data" / "skills-acl.yaml"


_env_override = os.environ.get("SKILLS_ACL_MANIFEST", "").strip()
_MANIFEST_PATH: Path = Path(_env_override) if _env_override else _default_manifest_path()

RiskLevel = Literal["standard", "elevated", "critical"]
CrossDeptPolicy = Literal["requestable", "restricted", "blocked"]
DefaultGrant = Literal["time_limited_30d", "time_limited_90d", "permanent", "restricted_read_only"]


@dataclass(frozen=True)
class SkillAcl:
    slug: str
    name: str
    departments: list[str]          # ["*"] = everyone
    integrations: list[str]
    risk_level: RiskLevel
    cross_dept_policy: CrossDeptPolicy
    cross_dept_approvers: list[str]  # dept name(s) to find approvers in
    cross_dept_default_grant: DefaultGrant
    description: str

    @property
    def is_universal(self) -> bool:
        return "*" in self.departments

    def user_has_tier1_access(self, user_department: str) -> bool:
        """Return True if *user_department* grants automatic Tier 1 access."""
        if self.is_universal:
            return True
        ud = user_department.strip().lower()
        return any(d.strip().lower() == ud for d in self.departments)

    @property
    def owning_department(self) -> str:
        """First non-wildcard department is treated as the skill's home dept."""
        for d in self.departments:
            if d != "*":
                return d
        return ""


_manifest: dict[str, SkillAcl] = {}


def _load_manifest() -> dict[str, SkillAcl]:
    result: dict[str, SkillAcl] = {}
    if not _MANIFEST_PATH.exists():
        logger.warning("skills-acl.yaml not found at %s — ACL will be permissive", _MANIFEST_PATH)
        return result
    raw = yaml.safe_load(_MANIFEST_PATH.read_text(encoding="utf-8"))
    for slug, cfg in (raw or {}).get("skills", {}).items():
        depts = cfg.get("departments") or []
        result[slug] = SkillAcl(
            slug=slug,
            name=(cfg.get("name") or "").strip(),
            departments=depts,
            integrations=cfg.get("integrations") or [],
            risk_level=cfg.get("risk_level", "standard"),
            cross_dept_policy=cfg.get("cross_dept_policy", "requestable"),
            cross_dept_approvers=(
                cfg.get("cross_dept_approvers")
                or ([d for d in depts if d != "*"][:1])
            ),
            cross_dept_default_grant=cfg.get("cross_dept_default_grant", "time_limited_30d"),
            description=cfg.get("description", ""),
        )
    logger.info("Loaded %d skill ACL entries from %s", len(result), _MANIFEST_PATH)
    return result


def reload_manifest() -> None:
    global _manifest
    _manifest = _load_manifest()


# Load at import time
_manifest = _load_manifest()


# ── ACL resolution ─────────────────────────────────────────────────────────────

def get_skill_acl(slug: str) -> Optional[SkillAcl]:
    """Return the ACL config for *slug*, or None if not in the manifest."""
    return _manifest.get(slug)


def humanize_slug(slug: str) -> str:
    return " ".join(part.capitalize() for part in slug.split("-") if part)


def skill_display_name(acl: Optional[SkillAcl], slug: str = "") -> str:
    """Return persisted display name or a humanized slug fallback."""
    if acl and acl.name.strip():
        return acl.name.strip()
    key = acl.slug if acl else slug
    return humanize_slug(key) if key else ""


def all_skill_acls() -> dict[str, SkillAcl]:
    return dict(_manifest)


# Explicit allowlist of people who bypass per-skill access control entirely.
# Empty by default: unrestricted access should be granted deliberately, and a
# shipped default here would be a standing backdoor in every deployment.
# Set SUPER_ACCESS_EMAILS to a comma-separated list to populate it.
_SUPER_ACCESS_EMAILS: frozenset[str] = frozenset(
    e.strip().lower()
    for e in (os.environ.get("SUPER_ACCESS_EMAILS") or "").split(",")
    if e.strip()
)


def _is_executive_title(job_title: Optional[str]) -> bool:
    """Kept for backwards-compatibility — always returns False.

    Access is now controlled by _SUPER_ACCESS_EMAILS, not job titles.
    """
    return False


def is_super_user(email: Optional[str]) -> bool:
    """Return True if this email is on the unrestricted-access allowlist."""
    if not email:
        return False
    return email.strip().lower() in _SUPER_ACCESS_EMAILS


def resolve_tier1_slugs(
    user_department: str,
    job_title: Optional[str] = None,
    is_executive: bool = False,
    owned_slugs: frozenset[str] = frozenset(),
    collaborator_slugs: frozenset[str] = frozenset(),
    email: Optional[str] = None,
) -> set[str]:
    """Return the set of skill slugs the user gets automatically (Tier 1).

    Users in _SUPER_ACCESS_EMAILS receive unrestricted access to every skill.

    Critical-risk skills are never granted via department membership alone —
    only the skill owner or an assigned collaborator receives automatic Tier 1
    access.  All other users must request access through the admin approval flow.
    """
    if is_super_user(email):
        return set(_manifest.keys())
    managed_slugs = owned_slugs | collaborator_slugs
    result: set[str] = set()
    for slug, acl in _manifest.items():
        if acl.risk_level == "critical":
            if slug in managed_slugs:
                result.add(slug)
        else:
            if slug in managed_slugs or acl.user_has_tier1_access(user_department):
                result.add(slug)
    return result


@dataclass
class SkillAccessResult:
    """Access decision for a single skill for a given user."""

    slug: str
    acl: Optional[SkillAcl]

    # Tier 1 — automatic home-dept access
    tier1: bool = False

    # Tier 2 — active approved cross-dept grant
    tier2: bool = False
    grant_id: Optional[str] = None
    expires_at: Optional[str] = None        # ISO string
    restrictions: Optional[dict] = None

    # Pending request (user already submitted, awaiting approval)
    pending_request_id: Optional[str] = None

    @property
    def accessible(self) -> bool:
        return self.tier1 or self.tier2

    @property
    def access_source(self) -> str:
        if self.tier1:
            return "home_department"
        if self.tier2:
            return "cross_dept_grant"
        return "none"

    @property
    def can_request(self) -> bool:
        if self.accessible:
            return False
        if self.acl is None:
            return True
        return self.acl.cross_dept_policy in ("requestable", "restricted")


def resolve_skills_for_user(
    user_department: str,
    active_grants: list[dict],          # dicts from grant_to_dict()
    pending_requests: list[dict],        # dicts from grant_to_dict()
    all_slugs: list[str],               # full list of known skill slugs
    job_title: Optional[str] = None,
    is_executive: bool = False,
    owned_slugs: frozenset[str] = frozenset(),
    collaborator_slugs: frozenset[str] = frozenset(),
    email: Optional[str] = None,
) -> list[SkillAccessResult]:
    """Compute a SkillAccessResult for every known skill slug.

    Parameters
    ----------
    user_department:
        The person's department name as reported by the directory provider.
    active_grants:
        All approved (and not expired / revoked) grants for this user.
    pending_requests:
        All pending (awaiting approval) requests for this user.
    all_slugs:
        Complete list of skill slugs (from the filesystem + manifest).
    owned_slugs:
        Slugs of skills published by this user (approved SkillSubmission rows).
        Required for critical-risk skills — only owners get automatic Tier 1 access.
    collaborator_slugs:
        Slugs where this user is a skill collaborator (co-manager).
    email:
        The user's email address. Users in _SUPER_ACCESS_EMAILS receive access
        to every skill regardless of department or risk level.
    """
    import json

    tier1_slugs = resolve_tier1_slugs(
        user_department,
        job_title=job_title,
        is_executive=is_executive,
        owned_slugs=owned_slugs,
        collaborator_slugs=collaborator_slugs,
        email=email,
    )

    grant_by_slug: dict[str, dict] = {}
    for g in active_grants:
        grant_by_slug[g["skill_slug"]] = g

    pending_by_slug: dict[str, dict] = {}
    for r in pending_requests:
        pending_by_slug[r["skill_slug"]] = r

    results: list[SkillAccessResult] = []
    for slug in all_slugs:
        acl = _manifest.get(slug)
        result = SkillAccessResult(slug=slug, acl=acl)

        if slug in tier1_slugs:
            result.tier1 = True
        elif slug in grant_by_slug:
            g = grant_by_slug[slug]
            result.tier2 = True
            result.grant_id = g["id"]
            result.expires_at = g.get("expires_at")
            raw_r = g.get("restrictions") or "{}"
            try:
                result.restrictions = json.loads(raw_r) if isinstance(raw_r, str) else raw_r
            except (ValueError, TypeError):
                result.restrictions = {}

        if slug in pending_by_slug:
            result.pending_request_id = pending_by_slug[slug]["id"]

        results.append(result)

    return results
