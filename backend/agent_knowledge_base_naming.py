"""Resource naming and isolation rules for per-agent knowledge bases.

Every blob-store namespace and retrieval namespace name is derived here and
nowhere else, because two separate isolation boundaries depend on it:

1. **Per-agent isolation.** The agent UUID is embedded in both names, and the
   server — never the model, never the client — decides which name a request
   targets. See AGENT_KNOWLEDGE_BASE_SPEC.md §5.2 for why the retrieval tool
   handler resolves the data store from the agent row rather than accepting one
   as a tool argument.

2. **Per-environment isolation.** Staging and production are *namespaces in the
   same cloud project* sharing one service account, so names alone
   keep their resources apart. Without an environment discriminator, restoring
   or cloning a production database into staging would hand staging rows the
   same agent UUIDs — and therefore make staging read and delete production
   buckets and data stores. The suffix makes that collision impossible.

The environment suffix follows the convention already used for this platform's
other buckets
and reads ``APP_ENV``, the same
variable ``secret_vault.py`` uses to pick vault key names.
"""

from __future__ import annotations

import os
import re
from app_config import RESOURCE_PREFIX

# Namespace names are capped at 63
# characters. Budget: prefix + 36-char UUID + environment suffix.
_MAX_NAME_LEN = 63

# Non-production environments get an explicit suffix; production is bare so the
# existing prod-style naming stays stable if this is ever backfilled.
_PROD_ENV = "production"

_BUCKET_PREFIX = f"{RESOURCE_PREFIX}-kb-"
_DATA_STORE_PREFIX = "agent-kb-"

_AGENT_ID_RE = re.compile(r"^[0-9a-fA-F-]{8,36}$")


def current_env() -> str:
    """Normalised deployment environment: ``production``, ``staging``, ...

    Defaults to ``production`` to match ``secret_vault.py`` — an unset value must
    never silently resolve to a *different* environment's resources.
    """
    return (os.environ.get("APP_ENV") or _PROD_ENV).strip().lower() or _PROD_ENV


def _env_suffix() -> str:
    env = current_env()
    return "" if env == _PROD_ENV else f"-{env}"


def _validate_agent_id(agent_id: str) -> str:
    """Reject anything that is not a plain agent UUID.

    Resource names are built by concatenation, so an agent_id carrying a slash,
    a dot, or an upper-case character would either produce an invalid
    resource name or — worse — a name that collides with another agent's.
    """
    if not isinstance(agent_id, str) or not _AGENT_ID_RE.match(agent_id):
        raise ValueError(f"invalid agent_id for knowledge-base naming: {agent_id!r}")
    return agent_id.lower()


def bucket_name_for_agent(agent_id: str) -> str:
    """bucket for this agent's uploaded documents.

    namespace names may need to be globally unique in some backends, so the full
    UUID is used rather than a truncated slug — a truncated slug risks colliding
    with a bucket in someone else's project, which surfaces as an opaque 409.
    """
    name = f"{_BUCKET_PREFIX}{_validate_agent_id(agent_id)}{_env_suffix()}"
    if len(name) > _MAX_NAME_LEN:
        raise ValueError(f"derived bucket name exceeds {_MAX_NAME_LEN} chars: {name}")
    return name


def data_store_id_for_agent(agent_id: str) -> str:
    """Retrieval namespace id for this agent."""
    name = f"{_DATA_STORE_PREFIX}{_validate_agent_id(agent_id)}{_env_suffix()}"
    if len(name) > _MAX_NAME_LEN:
        raise ValueError(f"derived data store id exceeds {_MAX_NAME_LEN} chars: {name}")
    return name


def resource_labels(agent_id: str, agent_name: str | None = None) -> dict[str, str]:
    """Labels applied to the bucket, for per-agent cost attribution.

    Bucket labels do propagate to the Cloud Billing detailed export, so storage
    cost breaks out per agent cleanly. label values allow only lowercase
    letters, digits, dashes and underscores (max 63 chars), so the agent's
    display name is sanitised rather than passed through.
    """
    labels = {
        "agent_id": _validate_agent_id(agent_id),
        "purpose": "agent-knowledge-base",
        "environment": current_env(),
    }
    if agent_name:
        sanitised = re.sub(r"[^a-z0-9_-]+", "-", agent_name.strip().lower()).strip("-")
        if sanitised:
            labels["agent_name"] = sanitised[:63]
    return labels


def request_labels(agent_id: str, agent_name: str | None = None) -> dict[str, str]:
    """``user_labels`` for search/import requests.

    These aid Cloud Logging correlation but are **not** a reliable billing-export
    key (bills at project level by SKU). Per-agent query cost is
    accounted app-side instead — see AGENT_KNOWLEDGE_BASE_SPEC.md §9.
    """
    return resource_labels(agent_id, agent_name)


def is_owned_by_this_env(resource_name: str) -> bool:
    """Whether a bucket / data store name belongs to the current environment.

    Used as a guard before any destructive operation: deprovisioning reads the
    stored name from the database, and a database restored from another
    environment would otherwise point deletion at that environment's resources.
    """
    if not resource_name:
        return False
    suffix = _env_suffix()
    if suffix:
        return resource_name.endswith(suffix)
    # Production: the name must carry no other environment's suffix.
    return not re.search(r"-(staging|dev|development|test|sandbox)$", resource_name)
