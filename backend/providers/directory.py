"""Who the people in the organisation are.

Several features need to answer organisational questions: who approves this
access request, which department does this person belong to, who are the
administrators. Those questions have organisation-specific answers, so they are
asked through this interface rather than of any particular system of record.

The interface is deliberately small — four questions and a cache reset. Anything
richer (cost centres, reporting chains more than one level deep, employment
history) belongs in a deployment's own implementation, not here.

Reference implementations:

``FileDirectoryProvider``
    Reads people from a YAML or JSON file. Suitable for small deployments and
    for development, and the easiest thing to point at a periodic export from
    whatever the organisation actually uses.

``ClaimsDirectoryProvider``
    Assumes no directory exists at all. Everything known about a person is what
    their sign-in token asserted, recorded as they authenticate. Reporting lines
    are unknown, so approval routing falls back to the administrator group. This
    is the default because it requires no configuration.

``NullDirectoryProvider``
    Knows nobody. Directory-dependent features degrade to their unrestricted or
    admin-only behaviour.
"""

from __future__ import annotations

import json
import logging
import os
import threading
from abc import ABC, abstractmethod
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)

_ACTIVE_STATUSES = frozenset({"active", "employee", "confirmed", "active employee"})


@dataclass(frozen=True)
class Person:
    """One person, in the terms this platform needs.

    ``email`` is the identity key throughout the platform and the only required
    field. Every other field may be empty when the underlying source does not
    know it; callers must treat them as optional rather than assuming a rich
    directory exists.
    """

    email: str
    name: str = ""
    department: str = ""
    team: str = ""
    manager_email: Optional[str] = None
    manager_name: Optional[str] = None
    title: Optional[str] = None
    level: Optional[str] = None
    status: str = "active"
    groups: tuple[str, ...] = ()

    @property
    def is_active(self) -> bool:
        return (self.status or "").strip().lower() in _ACTIVE_STATUSES

    def in_group(self, group: str) -> bool:
        target = (group or "").strip().lower()
        return any(g.strip().lower() == target for g in self.groups)

    def to_public_dict(self, *, anonymize: bool = True) -> dict:
        """Serialise for an API response.

        Anonymised by default: callers that only need to show *what kind of*
        person is involved (an approver's seniority, say) should not receive
        their name and address. Pass ``anonymize=False`` deliberately.
        """
        if anonymize:
            return {
                "role_title": self.title or "Unknown",
                "position_level": self.level,
                "manager": None,
                "name": None,
                "email": None,
            }
        return {
            "email": self.email,
            "name": self.name,
            "role_title": self.title,
            "position_level": self.level,
            "manager": self.manager_name or self.manager_email,
            "department": self.department,
            "team": self.team,
        }


class DirectoryProvider(ABC):
    """Organisational lookups. Implementations must never raise on absence."""

    @abstractmethod
    async def get_person(self, email: str) -> Optional[Person]:
        """Return one person, or ``None`` if unknown."""

    @abstractmethod
    async def list_people(self) -> list[Person]:
        """Every person the directory knows about. May be empty."""

    async def department_managers(self, department: str) -> list[Person]:
        """People who can approve on behalf of ``department``.

        Default implementation: everyone in the department who is named as
        somebody's manager. Implementations backed by a real reporting hierarchy
        should override this with something exact.
        """
        people = [p for p in await self.list_people() if p.is_active]
        wanted = (department or "").strip().lower()
        in_dept = [p for p in people if (p.department or "").strip().lower() == wanted]
        manager_emails = {
            (p.manager_email or "").strip().lower() for p in in_dept if p.manager_email
        }
        return [p for p in people if p.email.strip().lower() in manager_emails]

    async def group_members(self, group: str) -> list[Person]:
        """Active members of a named group."""
        return [
            p for p in await self.list_people() if p.is_active and p.in_group(group)
        ]

    def invalidate_cache(self) -> None:
        """Drop any cached state. Safe to call at any time."""
        return


class NullDirectoryProvider(DirectoryProvider):
    """A directory that knows nobody."""

    async def get_person(self, email: str) -> Optional[Person]:
        return None

    async def list_people(self) -> list[Person]:
        return []


class ClaimsDirectoryProvider(DirectoryProvider):
    """Everything known about a person comes from their own sign-in token.

    The authentication layer calls :func:`record_claims` on each request. That
    builds a directory of people who have actually signed in — enough for group
    membership and display, but with no reporting lines. Approval routing that
    needs a manager will find none and must fall back to the administrator
    group; that fallback is the caller's responsibility, not this class's.
    """

    async def get_person(self, email: str) -> Optional[Person]:
        return _claims_cache.get((email or "").strip().lower())

    async def list_people(self) -> list[Person]:
        return list(_claims_cache.values())

    async def department_managers(self, department: str) -> list[Person]:
        return []


_claims_cache: dict[str, Person] = {}
_claims_lock = threading.Lock()


def record_claims(
    *,
    email: str,
    name: str = "",
    groups: tuple[str, ...] = (),
    department: str = "",
) -> None:
    """Note what a sign-in token asserted about its bearer.

    Called by the authentication layer. Cheap, idempotent, and never raises —
    a directory failure must not be able to block a request.
    """
    key = (email or "").strip().lower()
    if not key:
        return
    try:
        person = Person(
            email=key,
            name=name or "",
            department=department or "",
            groups=tuple(groups),
        )
        with _claims_lock:
            _claims_cache[key] = person
    except Exception:  # pragma: no cover - defensive
        logger.debug("failed to record directory claims", exc_info=True)


class FileDirectoryProvider(DirectoryProvider):
    """People loaded from a YAML or JSON file.

    Path comes from ``DIRECTORY_FILE`` (default ``data/directory.yaml``). The
    file is re-read when its modification time changes, so an export job can
    refresh it in place without a restart.

    Expected shape::

        people:
          - email: ada@example.org
            name: Ada Lovelace
            department: Engineering
            team: Platform
            manager_email: grace@example.org
            title: Principal Engineer
            status: active
            groups: [platform-admins]

    A bare list of person mappings is also accepted.
    """

    def __init__(self, path: str | os.PathLike[str] | None = None) -> None:
        self._path = Path(
            path or os.environ.get("DIRECTORY_FILE") or "data/directory.yaml"
        )
        self._lock = threading.Lock()
        self._by_email: dict[str, Person] = {}
        self._loaded_mtime: float | None = None

    def _read_file(self) -> list[dict]:
        if not self._path.exists():
            logger.warning("directory file %s does not exist", self._path)
            return []
        raw = self._path.read_text(encoding="utf-8")
        if self._path.suffix.lower() in (".yaml", ".yml"):
            import yaml  # imported lazily so JSON-only deployments need no YAML

            data = yaml.safe_load(raw) or {}
        else:
            data = json.loads(raw or "{}")
        if isinstance(data, list):
            return [d for d in data if isinstance(d, dict)]
        if isinstance(data, dict):
            people = data.get("people") or []
            return [d for d in people if isinstance(d, dict)]
        return []

    @staticmethod
    def _to_person(row: dict) -> Optional[Person]:
        email = str(row.get("email") or "").strip().lower()
        if not email:
            return None
        groups = row.get("groups") or []
        if isinstance(groups, str):
            groups = [groups]
        manager_email = row.get("manager_email")
        return Person(
            email=email,
            name=str(row.get("name") or ""),
            department=str(row.get("department") or ""),
            team=str(row.get("team") or ""),
            manager_email=str(manager_email).strip().lower() if manager_email else None,
            manager_name=row.get("manager_name") or row.get("manager"),
            title=row.get("title"),
            level=row.get("level"),
            status=str(row.get("status") or "active"),
            groups=tuple(str(g) for g in groups),
        )

    def _ensure_loaded(self) -> None:
        try:
            mtime = self._path.stat().st_mtime if self._path.exists() else None
        except OSError:
            mtime = None
        if self._by_email and mtime == self._loaded_mtime:
            return
        with self._lock:
            try:
                rows = self._read_file()
            except Exception:
                logger.exception("failed to read directory file %s", self._path)
                return
            people: dict[str, Person] = {}
            for row in rows:
                person = self._to_person(row)
                if person is not None:
                    people[person.email] = person
            self._by_email = people
            self._loaded_mtime = mtime
            logger.info("loaded %d people from %s", len(people), self._path)

    async def get_person(self, email: str) -> Optional[Person]:
        self._ensure_loaded()
        return self._by_email.get((email or "").strip().lower())

    async def list_people(self) -> list[Person]:
        self._ensure_loaded()
        return list(self._by_email.values())

    def invalidate_cache(self) -> None:
        with self._lock:
            self._by_email = {}
            self._loaded_mtime = None
