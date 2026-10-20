"""Role derivation from a verified identity token.

The platform recognises three roles — administrator, member, read-only — and
derives them from group claims in the caller's token. Which group names carry
which role is deployment configuration, because no two identity providers name
groups alike:

    ADMIN_GROUPS=platform-admins,security-eng
    MEMBER_GROUPS=platform-users
    READONLY_GROUPS=platform-observers

Two behaviours make this workable across providers that do not all emit groups:

*Group names are matched case-insensitively.* Directory systems disagree about
case, and a role silently failing to apply because of it is a bad failure mode.

*Group membership is not required by default.* Many providers emit no group
claim at all. By default any authenticated caller is a member, and
administrators come from ``ADMIN_GROUPS`` or the ``ADMIN_EMAILS`` bootstrap
list. Set ``REQUIRE_GROUP_MEMBERSHIP=true`` to insist that callers hold a
recognised group, which is the stricter posture and the right one once groups
are actually being emitted.

``ADMIN_EMAILS`` exists so a fresh deployment has a way in before any groups are
wired up. It is a bootstrap mechanism, not a permission model — move to groups
once you have them.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from typing import FrozenSet


def _group_set(env_var: str, default: str = "") -> FrozenSet[str]:
    """Parse a comma-separated group list into a lower-cased set."""
    raw = os.environ.get(env_var)
    if raw is None:
        raw = default
    return frozenset(
        part.strip().lower() for part in raw.split(",") if part and part.strip()
    )


ADMIN_GROUPS: FrozenSet[str] = _group_set("ADMIN_GROUPS", "platform-admins")
MEMBER_GROUPS: FrozenSet[str] = _group_set("MEMBER_GROUPS", "platform-users")
READONLY_GROUPS: FrozenSet[str] = _group_set("READONLY_GROUPS")

#: Bootstrap administrators by address, for deployments with no group claims yet.
ADMIN_EMAILS: FrozenSet[str] = frozenset(
    part.strip().lower()
    for part in (os.environ.get("ADMIN_EMAILS") or "").split(",")
    if part and part.strip()
)

#: When false, any authenticated caller is at least a member.
REQUIRE_GROUP_MEMBERSHIP: bool = (
    os.environ.get("REQUIRE_GROUP_MEMBERSHIP", "").strip().lower()
    in ("1", "true", "yes", "on")
)

RECOGNISED_GROUPS: FrozenSet[str] = ADMIN_GROUPS | MEMBER_GROUPS | READONLY_GROUPS


@dataclass(frozen=True)
class AuthContext:
    """Identity derived from a verified token."""

    sub: str
    email: str
    groups: tuple[str, ...]

    @property
    def _lower_groups(self) -> set[str]:
        return {g.strip().lower() for g in self.groups if g and g.strip()}

    @property
    def is_admin(self) -> bool:
        if self.email and self.email.strip().lower() in ADMIN_EMAILS:
            return True
        return bool(self._lower_groups & ADMIN_GROUPS)

    @property
    def is_readonly(self) -> bool:
        """Read-only applies only when no stronger role also does."""
        if self.is_admin:
            return False
        groups = self._lower_groups
        if groups & MEMBER_GROUPS:
            return False
        return bool(groups & READONLY_GROUPS)

    @property
    def primary_role(self) -> str:
        if self.is_admin:
            return "admin"
        if self.is_readonly:
            return "readonly"
        if self._lower_groups & MEMBER_GROUPS or not REQUIRE_GROUP_MEMBERSHIP:
            return "member"
        return "unknown"

    @property
    def may_mutate_http(self) -> bool:
        """Whether this caller may issue writes. Read-only callers may not."""
        return self.primary_role in ("admin", "member")


def canonical_app_groups(raw: list[str]) -> tuple[str, ...]:
    """Normalise a raw groups claim into a stable, de-duplicated tuple."""
    return tuple(
        sorted({g.strip() for g in raw if isinstance(g, str) and g.strip()})
    )


def has_app_access(groups: tuple[str, ...]) -> bool:
    """Whether these groups are enough to use the application at all."""
    if not REQUIRE_GROUP_MEMBERSHIP:
        return True
    lowered = {g.strip().lower() for g in groups if g and g.strip()}
    return bool(lowered & RECOGNISED_GROUPS)


def default_member_group() -> str:
    """A group name that grants member access, for synthesising a dev identity."""
    return next(iter(sorted(MEMBER_GROUPS)), "platform-users")
