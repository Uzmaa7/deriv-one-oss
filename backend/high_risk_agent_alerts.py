"""Slack alerting when an agent is registered/edited with a high-or-critical-risk skill.

Fires a one-shot Slack notification (no debounce/state machine — this is a
discrete event, not a recurring health check) whenever a skill attached to an
agent resolves, via skill_system_links_model, to a catalogued system whose
risk_level is "critical", "high", or "elevated". Fails silent on any error —
a Slack outage or misconfiguration must never block agent registration/edit.
"""

from __future__ import annotations

import logging
import os
from typing import Any

from secret_vault import SecretVaultError, get_vault
from skill_system_links_model import SkillSystemLink, get_system_links_map

logger = logging.getLogger(__name__)

HIGH_RISK_LEVELS = {"critical", "high", "elevated"}

HIGH_RISK_AGENT_ALERTS_SLACK_CHANNEL_ID: str = os.environ.get(
    "HIGH_RISK_AGENT_ALERTS_SLACK_CHANNEL_ID", ""
)


def get_high_risk_links(db, skill_ids: list[str]) -> dict[str, list[SkillSystemLink]]:
    """skill_slug -> its links whose risk_level is high/critical/elevated.

    Only skills with at least one such link are included in the result.
    """
    if not skill_ids:
        return {}
    links_map = get_system_links_map(db, skill_ids)
    result: dict[str, list[SkillSystemLink]] = {}
    for slug, links in links_map.items():
        risky = [l for l in links if (l.risk_level or "").strip().lower() in HIGH_RISK_LEVELS]
        if risky:
            result[slug] = risky
    return result


def _get_platform_bot_token() -> str | None:
    from agent_channel_configs import get_platform_agent

    agent = get_platform_agent()
    if not agent or not agent.xoxb_secret_ref:
        return None
    try:
        return get_vault().read(agent.xoxb_secret_ref)
    except SecretVaultError:
        return None


def _format_pic_mention(token: str, creator_email: str) -> str | None:
    """`<@U…>` when the PIC's email resolves to a Slack user, else None."""
    from agent_failure_alerts import _lookup_slack_user_id_by_email

    uid = _lookup_slack_user_id_by_email(token, creator_email)
    return f"<@{uid}>" if uid else None


def _format_high_risk_message(
    agent,
    high_risk_links: dict[str, list[SkillSystemLink]],
    *,
    pic_mention: str | None = None,
) -> str:
    lines = [
        ":warning: *High/critical-risk skill attached to agent*",
        f"Agent: *{agent.name}*",
        f"Description: {agent.description or '(none)'}",
        f"PIC: {pic_mention or agent.creator_email or '(unknown)'}",
        "Skills & systems:",
    ]
    for slug, links in high_risk_links.items():
        for link in links:
            lines.append(f"• `{slug}` → {link.system_name} (*{link.risk_level}*)")
    return "\n".join(lines)


def _post_slack_message(token: str, channel: str, text: str) -> str | None:
    try:
        from slack_sdk import WebClient

        kwargs: dict[str, Any] = {"channel": channel, "text": text}
        resp = WebClient(token=token).chat_postMessage(**kwargs)
        return str(resp.get("ts") or "") or None
    except Exception:
        logger.warning("high_risk_agent_alerts: Slack post failed", exc_info=True)
        return None


def notify_high_risk_skills(agent, high_risk_links: dict[str, list[SkillSystemLink]]) -> None:
    """Fire-and-forget Slack post. Swallows all errors (never blocks agent save)."""
    if not high_risk_links:
        return
    try:
        channel = HIGH_RISK_AGENT_ALERTS_SLACK_CHANNEL_ID
        if not channel:
            logger.warning(
                "high_risk_agent_alerts: HIGH_RISK_AGENT_ALERTS_SLACK_CHANNEL_ID not configured, skipping"
            )
            return
        token = _get_platform_bot_token()
        if not token:
            logger.warning("high_risk_agent_alerts: no platform bot token available, skipping")
            return
        pic_mention = _format_pic_mention(token, getattr(agent, "creator_email", "") or "")
        text = _format_high_risk_message(agent, high_risk_links, pic_mention=pic_mention)
        _post_slack_message(token, channel, text)
    except Exception:
        logger.warning("high_risk_agent_alerts: notify_high_risk_skills failed", exc_info=True)
