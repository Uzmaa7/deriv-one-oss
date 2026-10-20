"""Organisational lookups, as the rest of the application sees them.

This is the seam between application code and whatever the deployment uses as
its system of record for people. Application modules import from here; they do
not know which :class:`~providers.directory.DirectoryProvider` is configured,
and they must not care.

Everything here fails soft. A directory is an external dependency, and an
outage of it must degrade a feature rather than break a request: lookups return
``None`` or an empty list and log, they never raise. Callers are expected to
have a sensible answer for "the directory does not know" — usually falling back
to administrator approval — because that is also the answer when no directory is
configured at all, which is the default.
"""

from __future__ import annotations

import logging
from typing import Optional

from providers import get_directory
from providers.directory import Person

logger = logging.getLogger(__name__)

__all__ = [
    "Person",
    "get_admin_group_members",
    "get_all_employees",
    "get_department_managers",
    "get_employee_by_email",
    "get_org_structure",
    "get_team_members",
    "invalidate_directory_cache",
    "person_to_public_dict",
]


async def get_employee_by_email(email: str) -> Optional[Person]:
    """Look up one person. ``None`` if unknown or the directory is unavailable."""
    if not email:
        return None
    try:
        return await get_directory().get_person(email)
    except Exception:
        logger.exception("directory lookup failed for %s", email)
        return None


async def get_all_employees() -> list[Person]:
    """Everyone the directory knows. Empty when it knows nobody."""
    try:
        return await get_directory().list_people()
    except Exception:
        logger.exception("directory enumeration failed")
        return []


async def get_department_managers(department: str) -> list[Person]:
    """People who can approve on behalf of ``department``.

    An empty result is normal and means "route to administrators instead" —
    it is what a claims-only directory always returns, since a sign-in token
    does not describe a reporting line.
    """
    if not department:
        return []
    try:
        return await get_directory().department_managers(department)
    except Exception:
        logger.exception("manager lookup failed for department %s", department)
        return []


async def get_admin_group_members(groups: tuple[str, ...] | list[str]) -> list[Person]:
    """Active members of any of ``groups``, de-duplicated by email."""
    directory = get_directory()
    found: dict[str, Person] = {}
    for group in groups or ():
        try:
            for person in await directory.group_members(group):
                found.setdefault(person.email, person)
        except Exception:
            logger.exception("group member lookup failed for %s", group)
    return list(found.values())


def person_to_public_dict(person: Person, *, anonymize: bool = True) -> dict:
    """Serialise a person for an API response. Anonymised unless told otherwise."""
    return person.to_public_dict(anonymize=anonymize)


def invalidate_directory_cache() -> None:
    """Force the next lookup to re-read the underlying source."""
    try:
        get_directory().invalidate_cache()
    except Exception:
        logger.exception("failed to invalidate directory cache")


async def get_org_structure() -> list[dict]:
    """The organisation as departments and the teams inside them.

    Derived from whatever the directory reports rather than stored separately,
    so it cannot drift from the people it describes. Departments with no team
    breakdown come back with an empty ``teams`` list, which is normal — many
    directories only go one level deep.

    Returns ``[{"department": str, "teams": [str], "headcount": int}]``,
    sorted by department name.
    """
    people = [p for p in await get_all_employees() if p.is_active]
    grouped: dict[str, dict] = {}
    for person in people:
        dept = (person.department or "").strip()
        if not dept:
            continue
        entry = grouped.setdefault(dept, {"department": dept, "teams": set(), "headcount": 0})
        entry["headcount"] += 1
        team = (person.team or "").strip()
        if team:
            entry["teams"].add(team)
    return [
        {"department": e["department"], "teams": sorted(e["teams"]), "headcount": e["headcount"]}
        for e in sorted(grouped.values(), key=lambda x: x["department"].lower())
    ]


async def get_team_members(department: str, team: str = "") -> list[Person]:
    """Active people in a department, optionally narrowed to one team.

    An empty ``team`` means the whole department. Matching is case-insensitive
    because directories are inconsistent about it and a silently empty roster is
    a confusing failure.
    """
    dept_key = (department or "").strip().lower()
    team_key = (team or "").strip().lower()
    if not dept_key:
        return []
    out = []
    for person in await get_all_employees():
        if not person.is_active:
            continue
        if (person.department or "").strip().lower() != dept_key:
            continue
        if team_key and (person.team or "").strip().lower() != team_key:
            continue
        out.append(person)
    return out
