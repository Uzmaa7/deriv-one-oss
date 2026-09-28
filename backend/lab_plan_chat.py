"""Conversational plan-mode agent for Lab — no code writing."""

from __future__ import annotations

from app_config import APP_NAME

import json
import logging
import os
from dataclasses import dataclass
from typing import Awaitable, Callable, Optional
from model_catalog import platform_default_model

logger = logging.getLogger(__name__)

_PLAN_CHAT_MODEL = os.environ.get("LAB_PLAN_MODEL", platform_default_model())

UPDATE_PLAN_TOOL = {
    "name": "update_plan",
    "description": (
        "Save or update the structured implementation plan. Call whenever the plan changes "
        "based on user answers. Include 2-6 features in build order."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "summary": {
                "type": "string",
                "description": "One-paragraph summary of what will be built.",
            },
            "features": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "title": {"type": "string"},
                        "description": {"type": "string"},
                        "files": {
                            "type": "array",
                            "items": {"type": "string"},
                        },
                    },
                    "required": ["title", "description"],
                },
            },
            "api_functions": {
                "type": "array",
                "items": {"type": "string"},
            },
            "open_questions": {
                "type": "array",
                "items": {"type": "string"},
                "description": (
                    "Business-scope questions only (screens, fields, audit events). "
                    "MUST be empty when only platform auth/storage/admin questions remain. "
                    "Never ask about SSO provider, login UI, admin config files, or database choice."
                ),
            },
        },
        "required": ["summary", "features"],
    },
}

MARK_PLAN_READY_TOOL = {
    "name": "mark_plan_ready",
    "description": (
        "Call when the plan is complete and you have enough information to start building. "
        "Only call after update_plan and when open_questions would be empty."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "confirmation_message": {
                "type": "string",
                "description": "Short message telling the user you will start building.",
            },
        },
        "required": ["confirmation_message"],
    },
}


@dataclass
class PlanChatResult:
    final_text: str
    success: bool
    plan_ready: bool = False
    plan_updated: bool = False


def _plan_system_prompt(app) -> str:
    from api_apps import _load_classification_meta, _load_json_list, _load_skill
    from lab_prompts import PLAN_MODE_PLATFORM_RULES

    tech = getattr(app, "tech_stack", "python") or "python"
    meta = _load_classification_meta(app)
    existing_plan = meta.get("build_plan")

    skills_block = ""
    for slug in _load_json_list(app.skill_ids):
        content = _load_skill(slug)
        if content:
            skills_block += f"\n- **{slug}**: {content[:400]}"

    plan_ctx = ""
    if existing_plan:
        plan_ctx = f"\n\nCurrent draft plan:\n{json.dumps(existing_plan, indent=2)[:3000]}"

    return f"""You are a senior product engineer in **Plan mode** for {APP_NAME} Lab.

Your job is to help the user refine an implementation plan BEFORE any code is written.
You must NOT write code or claim files were created.

App: {app.name}
Description: {app.description or "(none)"}
Tech stack: {tech} ({'React/Vite multi-page' if tech == 'react' else 'vanilla HTML/JS + Python backend'})
Attached skills:{skills_block or ' none'}
{plan_ctx}

{PLAN_MODE_PLATFORM_RULES}

WORKFLOW:
1. On the FIRST turn, call update_plan with an initial plan that already assumes platform SSO, /me, Share-panel admins, and Records API.
   Ask 0-3 questions ONLY about business scope (screens, audit events, search behavior). If the description is clear, use open_questions: [].
2. On follow-up turns, update the plan or call mark_plan_ready when business details are sufficient.
3. In your visible reply: summarize the plan; explain what the platform handles automatically; do not ask login or admin-infrastructure questions.
4. If the user says "use SSO" or pastes /me instructions, treat that as confirmation — update the plan and proceed; do not re-ask how login works.
5. Never use write_file or discuss file contents — planning only.

When calling update_plan, include summary, features, api_functions if needed, and open_questions (business only)."""


async def run_plan_chat(
    app_id: str,
    message: str,
    history: list[dict],
    owner_sub: str,
    on_event: Callable[[str, dict], Awaitable[None]],
) -> PlanChatResult:
    from api_apps import (
        _MODEL_GATEWAY_API_KEY,
        _MODEL_GATEWAY_URL,
        _MODEL,
        _compress_history,
        _serialize_block,
    )
    from apps_models import App
    from database import SessionLocal
    from lab_plan import set_plan_ready, store_build_plan

    if not _MODEL_GATEWAY_API_KEY:
        await on_event("error", {"message": "LLM not configured."})
        return PlanChatResult("", False)

    db = SessionLocal()
    try:
        app = db.get(App, app_id)
        if not app or app.owner_sub != owner_sub:
            await on_event("error", {"message": "App not found."})
            return PlanChatResult("", False)
        app_id_val = app.id
        system_prompt = _plan_system_prompt(app)
    finally:
        db.close()

    import anthropic
    from agent_llm import cached_system, cached_tools, with_trailing_cache_breakpoint

    client = anthropic.AsyncAnthropic(api_key=_MODEL_GATEWAY_API_KEY, base_url=_MODEL_GATEWAY_URL)
    tools = cached_tools([UPDATE_PLAN_TOOL, MARK_PLAN_READY_TOOL], model=_PLAN_CHAT_MODEL)
    system_cached = cached_system(system_prompt, model=_PLAN_CHAT_MODEL)

    from api_apps import _prepare_chat_history_for_llm

    messages = _prepare_chat_history_for_llm(
        [*history, {"role": "user", "content": message}],
    )

    from token_tracking import TokenAccumulator, record_token_usage
    usage_acc = TokenAccumulator()

    final_text = ""
    plan_ready = False
    plan_updated = False

    try:
        for _turn in range(4):
            assistant_content: list[dict] = []
            has_tool_use = False

            async with client.messages.stream(
                model=_PLAN_CHAT_MODEL,
                max_tokens=4096,
                system=system_cached,
                messages=with_trailing_cache_breakpoint(messages, model=_PLAN_CHAT_MODEL),
                tools=tools,
                tool_choice={"type": "any"} if _turn == 0 else {"type": "auto"},
            ) as stream:
                async for chunk in stream.text_stream:
                    final_text += chunk
                    await on_event("text", {"delta": chunk})
                final_message = await stream.get_final_message()
            usage_acc.add(final_message.usage)

            for block in final_message.content:
                assistant_content.append(_serialize_block(block))
                if getattr(block, "type", "") == "tool_use":
                    has_tool_use = True

            messages.append({"role": "assistant", "content": assistant_content})

            if not has_tool_use:
                break

            tool_results = []
            for block in final_message.content:
                if getattr(block, "type", "") != "tool_use":
                    continue
                name = block.name
                inp = block.input or {}
                result = ""

                if name == "update_plan":
                    from lab_plan import _normalize_plan
                    plan = _normalize_plan({
                        "features": inp.get("features") or [],
                        "api_functions": inp.get("api_functions") or [],
                    })
                    plan["summary"] = str(inp.get("summary") or "").strip()[:2000]
                    open_q = inp.get("open_questions") or []
                    if isinstance(open_q, list):
                        plan["open_questions"] = [str(q).strip() for q in open_q if str(q).strip()][:10]
                    store_build_plan(app_id_val, plan)
                    plan_updated = True
                    await on_event("plan_updated", {
                        "features": [f.get("title", "") for f in plan.get("features", [])],
                        "summary": plan.get("summary", ""),
                    })
                    result = "Plan saved."

                elif name == "mark_plan_ready":
                    set_plan_ready(app_id_val, True)
                    plan_ready = True
                    confirm = str(inp.get("confirmation_message") or "Plan approved — starting build.")
                    await on_event("plan_ready", {"message": confirm})
                    result = "Plan marked ready. Agent mode will begin implementation."

                tool_results.append({
                    "type": "tool_result",
                    "tool_use_id": block.id,
                    "content": result,
                })

            messages.append({"role": "user", "content": tool_results})
            if plan_ready:
                break

    except Exception:
        logger.exception("Plan chat failed for app %s", app_id_val)
        await on_event("error", {"message": "An error occurred while planning."})
        return PlanChatResult(final_text, False, plan_ready, plan_updated)

    db2 = SessionLocal()
    try:
        app_row = db2.get(App, app_id_val)
        if app_row:
            app_row.chat_history = json.dumps(_compress_history(messages))
            db2.commit()
    finally:
        db2.close()

    record_token_usage("lab_plan", _PLAN_CHAT_MODEL, usage_acc)
    return PlanChatResult(final_text, True, plan_ready, plan_updated)
