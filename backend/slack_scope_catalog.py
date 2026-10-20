"""Catalog of Slack bot scopes the platform is willing to grant to an auto-created agent.

This is the single source of truth used by:

* ``scope_router.infer_scopes_for_agent``   — the AI proposes a *subset* of the
  conditional scopes below based on the agent's purpose.
* ``scope_router.adjudicate_scope_request``  — when a user asks for an extra
  scope, the AI may only allow scopes that exist in this catalog.
* ``api_slack_oauth._finalize_approved_scopes`` — server-side enforcement: the
  final scope set must be ``BASE_SCOPES ∪ (subset of CONDITIONAL_SCOPES)``.

Design notes
────────────
- ``BASE_SCOPES`` is the runtime minimum for a basic @mention bot in public
  channels (be mentioned, reply, list public channels, resolve user names).
  These are **locked**: the user cannot remove them in the review UI and the
  server force-includes them.
- ``CONDITIONAL_SCOPES`` are capability-gated. Each entry carries a short
  ``capability`` phrase the LLM matches against the agent's stated usecase. The
  AI is instructed to default to *not* granting — precision over recall.
  Private-channel listing (``groups:read``), email lookup (``users:read.email``),
  message history (``channels:history``, ``groups:history``), and DM (``im:*``)
  scopes live here — not in ``BASE_SCOPES``.
- ``DENIED_SCOPES`` (e.g. ``chat:write.customize``) are never granted.
- Anything not in this catalog is never granted, regardless of LLM output or
  user request.
"""

from __future__ import annotations

from typing import Optional


# ── Base (locked) scopes ──────────────────────────────────────────────────────

# The runtime minimum for a basic @mention bot in public channels. Without
# these the bot cannot receive mentions, reply, list public channels, or resolve
# invoking users by name.
#
# Private-channel listing (groups:read), email lookup (users:read.email),
# message history (channels:history / groups:history), and DM (im:*) scopes are
# NOT base — the AI proposes them only when the usecase calls for them.
BASE_SCOPES: tuple[str, ...] = (
    "app_mentions:read",   # receive @mentions in channels
    "chat:write",          # post replies
    "channels:read",       # list public channels and membership
    "users:read",          # resolve Slack user ids → names
)

_BASE_SET: frozenset[str] = frozenset(BASE_SCOPES)

# Scopes that must never be granted, even if requested or returned by the LLM.
DENIED_SCOPES: frozenset[str] = frozenset({
    "chat:write.customize",  # overrides sender identity — never appropriate for agents
})

_DENIED_SET: frozenset[str] = DENIED_SCOPES


# ── Conditional (AI-proposed) scopes ──────────────────────────────────────────

class ScopeEntry:
    __slots__ = ("scope", "capability", "description")

    def __init__(self, scope: str, capability: str, description: str = "") -> None:
        self.scope = scope
        self.capability = capability
        self.description = description or capability

    def as_dict(self) -> dict:
        return {
            "scope": self.scope,
            "capability": self.capability,
            "description": self.description,
        }


# Ordered for a stable, human-readable report. The capability string is what the
# LLM sees; keep it short and verb-led ("read channel history", "upload files").
# NOTE: channels:read is BASE (public channel listing only). groups:read,
# users:read.email, message history, and im:* scopes are conditional.
CONDITIONAL_SCOPES: tuple[ScopeEntry, ...] = (
    ScopeEntry("channels:history",     "read message history in public channels the bot is in",
               "Read existing messages in public channels (not needed if the bot only posts)."),
    ScopeEntry("groups:history",       "read message history in private channels the bot is in",
               "Read existing messages in private channels (not needed if the bot only posts)."),
    ScopeEntry("groups:read",          "list private channels and membership",
               "Read basic information about private channels the bot is added to."),
    ScopeEntry("users:read.email",     "view user email addresses",
               "Access user email addresses to match workspace members against internal IAM/directory."),
    ScopeEntry("im:read",            "enumerate DM conversations",
               "List the bot's DM conversations."),
    ScopeEntry("im:history",         "read DM message history",
               "Read messages in DMs (needed for DM trigger mode)."),
    ScopeEntry("im:write",           "open a DM with a user",
               "Open a new DM with a user (proactive DM)."),
    # NOTE: posting messages uses chat:write (in BASE) — there is no
    # channels:write / groups:write scope; Slack rejects them as illegal.
    # channels:join lets the bot self-join PUBLIC channels (private channels
    # require an invite; there is no groups:join bot scope).
    ScopeEntry("channels:join",      "join public channels on its own",
               "Self-join public channels without an invite."),
    ScopeEntry("channels:manage",    "create, archive, or rename public channels",
               "Create, archive, or rename public channels."),
    ScopeEntry("groups:manage",      "create, archive, or rename private channels",
               "Create, archive, or rename private channels."),
    ScopeEntry("mpim:read",          "read multi-person DM names and membership",
               "List multi-person DM conversations."),
    ScopeEntry("mpim:history",       "read multi-person DM history",
               "Read messages in multi-person DMs."),
    ScopeEntry("files:read",         "read/download files shared in conversations",
               "Download files users share with the bot."),
    ScopeEntry("files:write",        "upload and share files",
               "Upload files into conversations (e.g. PDF/CSV reports)."),
    ScopeEntry("search:read",        "search messages and files across the workspace",
               "Workspace-wide search of messages and files."),
    ScopeEntry("team:read",          "read workspace/team metadata",
               "Read workspace name, domain, and team-level metadata."),
    ScopeEntry("reactions:read",     "read emoji reactions on messages",
               "See emoji reactions on messages."),
    ScopeEntry("reactions:write",    "add emoji reactions to messages",
               "Add emoji reactions to messages."),
    ScopeEntry("pins:write",         "pin messages in channels",
               "Pin messages in channels."),
    ScopeEntry("bookmarks:write",    "add bookmarks to channels",
               "Add bookmarks to channels."),
    ScopeEntry("reminders:write",    "create and manage reminders for users",
               "Schedule reminders for users."),
    ScopeEntry("usergroups:read",    "read user groups (groups of users)",
               "List user groups and their membership."),
    ScopeEntry("usergroups:write",   "create and manage user groups",
               "Create and update user groups."),
    ScopeEntry("workflow.steps:execute", "run Slack Workflow Builder steps",
               "Execute steps in Slack Workflow Builder workflows."),
)

_CONDITIONAL_BY_SCOPE: dict[str, ScopeEntry] = {e.scope: e for e in CONDITIONAL_SCOPES}


# ── Public helpers ────────────────────────────────────────────────────────────

def all_known_scopes() -> frozenset[str]:
    """Every scope the platform will ever grant (base + conditional), minus denied."""
    return (_BASE_SET | frozenset(_CONDITIONAL_BY_SCOPE.keys())) - _DENIED_SET


def is_base_scope(scope: str) -> bool:
    return scope in _BASE_SET


def is_known_scope(scope: str) -> bool:
    return scope in all_known_scopes() and scope not in _DENIED_SET


def conditional_entry(scope: str) -> Optional[ScopeEntry]:
    return _CONDITIONAL_BY_SCOPE.get(scope)


def catalog_for_prompt() -> str:
    """Format the conditional catalog for the LLM prompt."""
    lines = [f"{e.scope}: {e.capability}" for e in CONDITIONAL_SCOPES]
    return "\n".join(lines)


def enforce_approved_set(approved: list[str]) -> tuple[list[str], list[str]]:
    """Sanitise a user-supplied approved scope list.

    Returns ``(final, dropped)`` where ``final`` is the de-duplicated, ordered
    list that is ``BASE_SCOPES`` plus any approved scopes that exist in the
    conditional catalog.  Anything unknown — or any base scope the user tried
    to drop (impossible from the UI, but defensive) — is handled here: base
    scopes are always force-included; unknown scopes are dropped and returned
    in ``dropped`` so the caller can log / surface them.
    """
    final: list[str] = list(BASE_SCOPES)
    seen: set[str] = set(_BASE_SET)
    dropped: list[str] = []
    for scope in approved or []:
        if not isinstance(scope, str):
            continue
        scope = scope.strip()
        if not scope:
            continue
        if scope in _DENIED_SET:
            dropped.append(scope)
            continue
        if scope in seen:
            continue
        if scope in _CONDITIONAL_BY_SCOPE:
            seen.add(scope)
            final.append(scope)
        else:
            dropped.append(scope)
    return final, dropped
