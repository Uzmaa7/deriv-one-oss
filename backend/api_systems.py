"""Connected-systems view — catalogue entries joined with the skills and
agents that touch them.

Endpoint
────────
GET /api/admin/systems   (admin only)

How the join works
──────────────────
The catalogue owns the system registry (
``allSystemInventories``), filtered to systems the system catalogue currently marks active —
terminated systems are excluded by ``system_catalog``. Skills are linked to systems by
``skill_system_links`` rows, resolved at publish/edit time by
``skill_system_matcher``. Agents have no direct system link — an agent is
"connected to" a system transitively, because it has a skill attached
(``agents.skill_ids``) that is linked to that system.

Response
────────
{
  "systems": [
    {
      "system_name": "Google Workspace",
      "risk_level": "Critical",
      "system_purpose": "...",          // may be null
      "sso_enabled": true,              // may be null
      "system_status": "Active",        // may be null
      "skills": [
        {"slug": "gmail-search", "name": "Gmail Search",
         "integration_tags": ["gmail"], "directions": ["read"],
         "match_confidence": "exact", "sources": ["integration_tag"]}
      ],
      "agents": [
        {"id": "…", "name": "Support Bot", "avatar_url": null, "status": "active",
         "via_skills": ["gmail-search"]}
      ],
      "skill_count": 1,
      "agent_count": 1
    }
  ],
  "unmatched_tags": [                   // integration tags no active system matched
    {"integration_tag": "some-tool", "skills": ["skill-slug"]}
  ],
  "stale_links": [                      // links to systems no longer active in the system catalogue
    {"system_name": "Jira", "skills": ["skill-slug"]}
  ],
  "totals": {"systems": 412, "connected_systems": 27, "skills": 61, "agents": 14},
  "registry_available": true,
  "fetched_at": "2026-08-21T09:00:00Z"
}
"""

from __future__ import annotations

import json
import logging
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy.orm import Session

from agents import Agent
from cf_access import build_auth_context
from database import SessionLocal
from skill_system_links_model import SkillSystemLink
from skills_acl import all_skill_acls, skill_display_name

logger = logging.getLogger(__name__)
router = APIRouter()


def _db() -> Session:
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def _admin_auth(request: Request):
    try:
        ctx = build_auth_context(request.headers)
    except Exception as exc:
        raise HTTPException(status_code=401, detail=str(exc)) from exc
    if not ctx.is_admin:
        raise HTTPException(status_code=403, detail="Admin access required.")
    return ctx


def _loads_list(raw: str | None) -> list[str]:
    try:
        value = json.loads(raw or "[]")
    except (TypeError, ValueError):
        return []
    return [str(v) for v in value] if isinstance(value, list) else []


_RISK_ORDER = {"critical": 3, "high": 2, "elevated": 2, "medium": 1, "low": 0}


def _risk_rank(risk_level: str | None) -> int:
    if not risk_level:
        return -1
    return _RISK_ORDER.get(risk_level.strip().lower(), 0)


@router.get("/api/admin/systems")
async def admin_systems(request: Request, db: Session = Depends(_db)):
    """Every catalogued system, with the skills linked to it and the agents
    that reach it through those skills."""
    _admin_auth(request)

    import system_catalog

    # ── 1. The connected-system catalogue: authoritative name and risk level.
    owner_dir: dict[str, dict] = {}
    inventory: dict[str, dict] = {}
    try:
        owner_dir = system_catalog.get_system_map()
        inventory = owner_dir
    except Exception:
        logger.exception("admin_systems: system catalogue lookup failed")

    registry: dict[str, dict] = {}
    for key, rec in owner_dir.items():
        registry[key] = {
            "system_name": rec.get("system_name") or key,
            "risk_level": rec.get("risk_level"),
        }
    for key, inv in inventory.items():
        entry = registry.setdefault(key, {"system_name": key, "risk_level": inv.get("risk_level")})
        if not entry.get("risk_level"):
            entry["risk_level"] = inv.get("risk_level")
        entry["system_purpose"] = inv.get("system_purpose")
        entry["sso_enabled"] = inv.get("sso_enabled")
        entry["mfa_enabled"] = inv.get("mfa_enabled")
        entry["system_status"] = inv.get("system_status")

    # ── 2. Skill → system links (skills already carry the system relationship). ──
    links = db.query(SkillSystemLink).all()
    acls = all_skill_acls()

    # system_key -> skill_slug -> aggregated link info
    by_system: dict[str, dict[str, dict]] = {}
    unmatched: dict[str, set[str]] = {}
    stale: dict[str, set[str]] = {}
    registry_available = bool(owner_dir or inventory)

    for link in links:
        slug = link.skill_slug
        if slug not in acls:
            # Orphaned link (skill unpublished / removed from the ACL manifest).
            continue
        if not link.system_name:
            unmatched.setdefault(link.integration_tag, set()).add(slug)
            continue

        key = link.system_name.strip().lower()
        if key not in registry:
            # The link points at a system the system catalogue no longer lists as active
            # (terminated, renamed, or removed). Don't invent a row for it —
            # report it as stale so a re-scan can clear it.
            if registry_available:
                stale.setdefault(link.system_name, set()).add(slug)
                continue
            # Registry unavailable: fall back to the cached link data rather
            # than showing an empty page.
            registry[key] = {"system_name": link.system_name, "risk_level": link.risk_level}
        entry = by_system.setdefault(key, {}).setdefault(slug, {
            "slug": slug,
            "name": skill_display_name(acls.get(slug), slug),
            "risk_level": getattr(acls.get(slug), "risk_level", None),
            "integration_tags": set(),
            "directions": set(),
            "confidences": set(),
            "sources": set(),
        })
        if link.integration_tag:
            entry["integration_tags"].add(link.integration_tag)
        if link.direction:
            entry["directions"].add(link.direction)
        if link.match_confidence:
            entry["confidences"].add(link.match_confidence)
        if link.source:
            entry["sources"].add(link.source)

    # ── 3. Agents → systems, transitively through their attached skills. ──────
    agent_rows = (
        db.query(Agent)
        .filter(Agent.status != "decommissioned")
        .order_by(Agent.name)
        .all()
    )
    # skill_slug -> [agent rows]
    agents_by_skill: dict[str, list[Agent]] = {}
    for agent in agent_rows:
        for slug in _loads_list(agent.skill_ids):
            agents_by_skill.setdefault(slug, []).append(agent)

    # ── 4. Assemble. ─────────────────────────────────────────────────────────
    systems: list[dict] = []
    connected_skill_slugs: set[str] = set()
    connected_agent_ids: set[str] = set()

    for key, meta in registry.items():
        skill_entries = by_system.get(key, {})

        skills_out = []
        for entry in sorted(skill_entries.values(), key=lambda e: e["name"].lower()):
            skills_out.append({
                "slug": entry["slug"],
                "name": entry["name"],
                "risk_level": str(entry["risk_level"]) if entry["risk_level"] else None,
                "integration_tags": sorted(entry["integration_tags"]),
                "directions": sorted(entry["directions"]),
                "match_confidence": sorted(entry["confidences"])[0] if entry["confidences"] else None,
                "sources": sorted(entry["sources"]),
            })
            connected_skill_slugs.add(entry["slug"])

        # An agent reaches this system via every attached skill linked to it.
        agents_out: dict[str, dict] = {}
        for slug in skill_entries:
            for agent in agents_by_skill.get(slug, []):
                out = agents_out.setdefault(agent.id, {
                    "id": agent.id,
                    "name": agent.name,
                    "avatar_url": agent.avatar_url,
                    "status": agent.status,
                    "via_skills": [],
                })
                out["via_skills"].append(slug)
                connected_agent_ids.add(agent.id)
        for out in agents_out.values():
            out["via_skills"].sort()

        systems.append({
            **meta,
            "skills": skills_out,
            "agents": sorted(agents_out.values(), key=lambda a: a["name"].lower()),
            "skill_count": len(skills_out),
            "agent_count": len(agents_out),
        })

    # Connected systems first, then by criticality, then alphabetically.
    systems.sort(key=lambda s: (
        0 if s["skill_count"] else 1,
        -_risk_rank(s.get("risk_level")),
        s["system_name"].lower(),
    ))

    return {
        "systems": systems,
        "unmatched_tags": [
            {"integration_tag": tag, "skills": sorted(slugs)}
            for tag, slugs in sorted(unmatched.items())
        ],
        "stale_links": [
            {"system_name": name, "skills": sorted(slugs)}
            for name, slugs in sorted(stale.items())
        ],
        "totals": {
            "systems": len(systems),
            "connected_systems": sum(1 for s in systems if s["skill_count"]),
            "skills": len(connected_skill_slugs),
            "agents": len(connected_agent_ids),
        },
        "registry_available": registry_available,
        "fetched_at": datetime.now(timezone.utc).isoformat(),
    }
