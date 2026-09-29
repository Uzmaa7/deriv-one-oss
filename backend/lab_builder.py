"""Lab builder agent loop — tools, plan-then-build, and verify integration."""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from typing import Awaitable, Callable, Optional

from fastapi import HTTPException

logger = logging.getLogger(__name__)

_MAX_TOOL_TURNS_PYTHON = 25
_MAX_TOOL_TURNS_REACT = 40

_CLAIMS_COMPLETION_RE = re.compile(
    r"\b(i(?:'ve| have)?\s+(?:created|implemented|added|built|fixed|updated|written)|"
    r"(?:done|finished|complete)[!.]?\s*$)",
    re.IGNORECASE | re.MULTILINE,
)

APPLY_PATCH_TOOL = {
    "name": "apply_patch",
    "description": (
        "Surgical edit: replace old_string with new_string in an existing file. "
        "old_string must match exactly once (include enough context). "
        "Prefer this over write_file for small changes. Must read_file first."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "path": {"type": "string", "description": "Relative file path."},
            "old_string": {"type": "string", "description": "Exact text to find (once)."},
            "new_string": {"type": "string", "description": "Replacement text."},
        },
        "required": ["path", "old_string", "new_string"],
    },
}

BUILD_REACT_TOOL = {
    "name": "build_react",
    "description": (
        "Compile the React/Vite project under __src__/ and publish dist/ to the app root. "
        "Call after editing __src__/ files. Returns build errors if compilation fails."
    ),
    "input_schema": {"type": "object", "properties": {}, "required": []},
}


@dataclass
class BuilderStats:
    writes: int = 0
    patches: int = 0
    builds: int = 0
    reads: int = 0


@dataclass
class BuilderResult:
    final_text: str
    files_written: list[str]
    success: bool
    stats: BuilderStats = field(default_factory=BuilderStats)
    src_touched: bool = False


def _max_turns(tech_stack: str) -> int:
    return _MAX_TOOL_TURNS_REACT if tech_stack == "react" else _MAX_TOOL_TURNS_PYTHON


def _builder_tools(tech_stack: str) -> list[dict]:
    from api_apps import (
        _LIST_FILES_TOOL,
        _READ_FILE_TOOL,
        _RUN_PYTHON_SNIPPET_TOOL,
        _WRITE_FILE_TOOL,
    )

    tools = [_WRITE_FILE_TOOL, APPLY_PATCH_TOOL, _READ_FILE_TOOL, _LIST_FILES_TOOL, _RUN_PYTHON_SNIPPET_TOOL]
    if tech_stack == "react":
        tools.append(BUILD_REACT_TOOL)
    return tools


def apply_patch_file(
    app_id: str,
    rel_path: str,
    old_string: str,
    new_string: str,
    *,
    files_read: set[str],
    existing_files: set[str],
) -> str:
    """Apply surgical patch. Returns result message for the model."""
    from api_apps import _read_app_file, _write_app_file

    rel = rel_path.strip()
    if rel in existing_files and rel not in files_read:
        return f"Error: read_file('{rel}') required before modifying an existing file."

    if not old_string:
        return "Error: old_string must not be empty."
    if old_string == new_string:
        return "Error: old_string and new_string are identical."

    try:
        content = _read_app_file(app_id, rel)
    except HTTPException as exc:
        return f"Error: {exc.detail}"

    count = content.count(old_string)
    if count == 0:
        return f"Error: old_string not found in {rel}."
    if count > 1:
        return f"Error: old_string found {count} times in {rel} — include more context."

    updated = content.replace(old_string, new_string, 1)
    try:
        _write_app_file(app_id, rel, updated)
    except (HTTPException, ValueError) as exc:
        err = getattr(exc, "detail", str(exc))
        return f"Error writing {rel}: {err}"
    return f"Patched: {rel}"


async def run_build_react(app_id: str, slug: str) -> tuple[bool, str, list[str]]:
    """Run Vite build. Returns (ok, message, dist_files_written)."""
    from node_build import build_react, upload_dist

    result = await build_react(app_id, slug)
    if not result.get("ok"):
        stage = result.get("stage", "build")
        stderr = (result.get("stderr") or "Unknown build error")[:3000]
        return False, f"Build failed ({stage}):\n{stderr}", []

    dist = result.get("dist") or {}
    written = upload_dist(app_id, dist) if dist else []
    return True, f"Build succeeded — {len(written)} file(s) published.", written


async def run_planned_implementation(
    app_id: str,
    owner_sub: str,
    on_event: Callable[[str, dict], Awaitable[None]],
    *,
    original_message: str = "",
) -> BuilderResult:
    """Implement an approved build plan feature-by-feature."""
    from api_apps import _load_classification_meta, _load_json_list
    from apps_models import App
    from database import SessionLocal
    from lab_plan import (
        feature_user_message,
        is_plan_implemented,
        load_build_plan,
        set_plan_implemented,
    )

    db = SessionLocal()
    try:
        app = db.get(App, app_id)
        if not app or app.owner_sub != owner_sub:
            await on_event("error", {"message": "App not found."})
            return BuilderResult("", [], False)
        meta = _load_classification_meta(app)
        plan = load_build_plan(meta)
        if not plan:
            await on_event("error", {"message": "No build plan found."})
            return BuilderResult("", [], False)
        if is_plan_implemented(meta):
            return BuilderResult("", [], True)
        history = _load_json_list(app.chat_history)
        kickoff = original_message or (app.description or "")
    finally:
        db.close()

    features = plan.get("features") or []
    combined_text: list[str] = []
    combined_files: list[str] = []
    stats = BuilderStats()
    running_history = list(history)

    for idx, feature in enumerate(features):
        await on_event("feature_start", {"title": feature.get("title", ""), "index": idx})
        feat_msg = feature_user_message(
            feature, plan, kickoff, feature_index=idx, total_features=len(features),
        )
        result = await run_lab_builder(
            app_id, feat_msg, running_history, owner_sub, on_event, skip_plan=True,
        )
        combined_text.append(result.final_text)
        combined_files.extend(result.files_written)
        stats.writes += result.stats.writes
        stats.patches += result.stats.patches
        stats.builds += result.stats.builds
        if not result.success:
            return BuilderResult(
                "\n\n".join(t for t in combined_text if t),
                combined_files,
                False,
                stats,
            )
        db2 = SessionLocal()
        try:
            refreshed = db2.get(App, app_id)
            if refreshed:
                from api_apps import _load_json_list as _ljl
                running_history = _ljl(refreshed.chat_history)
        finally:
            db2.close()

    set_plan_implemented(app_id, True)
    return BuilderResult(
        "\n\n".join(t for t in combined_text if t),
        combined_files,
        True,
        stats,
    )


async def run_lab_builder(
    app_id: str,
    message: str,
    history: list[dict],
    owner_sub: str,
    on_event: Callable[[str, dict], Awaitable[None]],
    *,
    skip_plan: bool = False,
) -> BuilderResult:
    """Main Lab builder loop with optional plan-then-build."""
    from api_apps import (
        _MODEL_GATEWAY_API_KEY,
        _MODEL_GATEWAY_URL,
        _MODEL,
        _accessible_skill_slugs,
        _build_system_prompt,
        _compress_history,
        _list_app_files,
        _prepare_chat_history_for_llm,
        _read_app_file,
        _run_snippet_for_builder,
        _serialize_block,
        _update_manifest,
        _write_app_file,
    )
    from apps_models import App
    from database import SessionLocal
    if not _MODEL_GATEWAY_API_KEY:
        await on_event("error", {"message": "LLM not configured."})
        return BuilderResult("", [], False)

    db = SessionLocal()
    try:
        app = db.get(App, app_id)
        if not app or app.owner_sub != owner_sub:
            await on_event("error", {"message": "App not found."})
            return BuilderResult("", [], False)
        tech_stack = app.tech_stack or "python"
        app_slug = app.slug
        app_id_val = app.id
    finally:
        db.close()

    # ── Single builder pass ──
    from api_apps import _extract_drive_urls_from_text, _attach_google_drive_source

    attach_notes: list[str] = []
    for file_id in _extract_drive_urls_from_text(message):
        try:
            entry = await _attach_google_drive_source(app_id_val, owner_sub, file_id)
            attach_notes.append(f"Attached Google {entry.get('source_type', 'file')} \"{entry.get('name')}\".")
        except Exception as exc:
            attach_notes.append(f"Could not attach Drive file {file_id}: {exc}")

    effective_message = message
    if attach_notes:
        effective_message = "\n".join(attach_notes) + "\n\n" + message

    existing_files = set(_list_app_files(app_id_val))
    accessible = await _accessible_skill_slugs(app.owner_sub, app.creator_email or "")
    system_prompt = _build_system_prompt(app, list(existing_files), accessible_slugs=accessible)
    max_turns = _max_turns(tech_stack)
    tools = _builder_tools(tech_stack)

    import anthropic
    from agent_llm import cached_system, cached_tools, with_trailing_cache_breakpoint

    client = anthropic.AsyncAnthropic(api_key=_MODEL_GATEWAY_API_KEY, base_url=_MODEL_GATEWAY_URL)
    tools_cached = cached_tools(tools, model=_MODEL)
    system_cached = cached_system(system_prompt, model=_MODEL)

    messages = _prepare_chat_history_for_llm(
        [*history, {"role": "user", "content": effective_message}],
    )

    from token_tracking import TokenAccumulator, record_token_usage as _record_usage
    usage_acc = TokenAccumulator()

    files_written: list[str] = []
    files_read: set[str] = set()
    stats = BuilderStats()
    final_assistant_text = ""
    any_tool_called = False
    any_write_succeeded = False
    src_touched = False
    turn_file_edits: list[dict] = []

    try:
        for turn in range(max_turns + 1):
            tool_choice: dict = {"type": "any"} if not any_tool_called else {"type": "auto"}
            assistant_content: list[dict] = []
            has_tool_use = False

            async with client.messages.stream(
                model=_MODEL,
                max_tokens=16000,
                system=system_cached,
                messages=with_trailing_cache_breakpoint(messages, model=_MODEL),
                tools=tools_cached,
                tool_choice=tool_choice,
            ) as stream:
                async for text_chunk in stream.text_stream:
                    final_assistant_text += text_chunk
                    await on_event("text", {"delta": text_chunk})
                final_message = await stream.get_final_message()
            usage_acc.add(final_message.usage)

            for block in final_message.content:
                serialized = _serialize_block(block)
                assistant_content.append(serialized)
                if serialized.get("type") == "tool_use":
                    has_tool_use = True

            messages.append({"role": "assistant", "content": assistant_content})

            if not has_tool_use:
                if final_message.stop_reason == "max_tokens" and turn < max_turns:
                    messages.append({
                        "role": "user",
                        "content": "[SYSTEM: Continue — call the tool you were about to use.]",
                    })
                    continue
                break

            any_tool_called = True
            tool_results = []

            for block in final_message.content:
                if getattr(block, "type", "") != "tool_use":
                    continue

                tool_name = block.name
                tool_input = block.input or {}
                result_content = ""

                if tool_name == "write_file":
                    rel_path = str(tool_input.get("path", "")).strip()
                    file_content = str(tool_input.get("content", ""))
                    if rel_path in existing_files and rel_path not in files_read:
                        result_content = f"Error: read_file('{rel_path}') required before overwriting."
                    else:
                        try:
                            from lab_edits import build_file_edit_summary
                            old_text = ""
                            if rel_path in existing_files:
                                try:
                                    old_text = _read_app_file(app_id_val, rel_path)
                                except HTTPException:
                                    old_text = ""
                            _write_app_file(app_id_val, rel_path, file_content)
                            files_written.append(rel_path)
                            existing_files.add(rel_path)
                            any_write_succeeded = True
                            stats.writes += 1
                            if rel_path.startswith("__src__/"):
                                src_touched = True
                            result_content = f"Written: {rel_path}"
                            edit = build_file_edit_summary(
                                rel_path,
                                kind="write",
                                old_text=old_text,
                                new_text=file_content,
                            )
                            turn_file_edits.append(edit)
                            await on_event("file_edit", edit)
                            await on_event("file_written", {"path": rel_path})
                        except (HTTPException, ValueError) as exc:
                            result_content = f"Error writing {rel_path}: {getattr(exc, 'detail', exc)}"
                            await on_event("warning", {"message": result_content})

                elif tool_name == "apply_patch":
                    rel_path = str(tool_input.get("path", "")).strip()
                    old_s = str(tool_input.get("old_string", ""))
                    new_s = str(tool_input.get("new_string", ""))
                    result_content = apply_patch_file(
                        app_id_val, rel_path, old_s, new_s,
                        files_read=files_read, existing_files=existing_files,
                    )
                    if result_content.startswith("Patched:"):
                        files_written.append(rel_path)
                        any_write_succeeded = True
                        stats.patches += 1
                        if rel_path.startswith("__src__/"):
                            src_touched = True
                        from lab_edits import build_file_edit_summary
                        edit = build_file_edit_summary(
                            rel_path, kind="patch", old_text=old_s, new_text=new_s,
                        )
                        turn_file_edits.append(edit)
                        await on_event("file_edit", edit)
                        await on_event("file_written", {"path": rel_path, "patch": True})
                    else:
                        await on_event("warning", {"message": result_content})

                elif tool_name == "read_file":
                    rel_path = str(tool_input.get("path", "")).strip()
                    try:
                        result_content = _read_app_file(app_id_val, rel_path)
                        files_read.add(rel_path)
                        stats.reads += 1
                    except HTTPException as exc:
                        result_content = f"Error: {exc.detail}"

                elif tool_name == "list_files":
                    file_list = _list_app_files(app_id_val)
                    result_content = "\n".join(file_list) if file_list else "(no files yet)"

                elif tool_name == "run_python_snippet":
                    code = str(tool_input.get("code", "")).strip()
                    if not code:
                        result_content = "[error] No code provided."
                    else:
                        await on_event("snippet_running", {"preview": code[:120]})
                        result_content = await _run_snippet_for_builder(app_id_val, code)
                        await on_event("snippet_result", {"preview": result_content[:200]})

                elif tool_name == "build_react":
                    ok, msg, built = await run_build_react(app_id_val, app_slug)
                    stats.builds += 1
                    result_content = msg
                    if ok:
                        files_written.extend(built)
                        any_write_succeeded = True
                        await on_event("build_complete", {"files": built})
                    else:
                        await on_event("warning", {"message": msg[:500]})

                tool_results.append({
                    "type": "tool_result",
                    "tool_use_id": block.id,
                    "content": result_content,
                })

            messages.append({"role": "user", "content": tool_results})

    except Exception as exc:
        logger.exception("Lab builder error for app %s", app_id_val)
        hint = str(exc).replace("\n", " ")[:240]
        await on_event(
            "error",
            {"message": f"An error occurred while building the app. ({hint})"},
        )
        return BuilderResult(final_assistant_text, files_written, False, stats, src_touched)

    if (
        not any_write_succeeded
        and final_assistant_text
        and _CLAIMS_COMPLETION_RE.search(final_assistant_text)
    ):
        warning = (
            "\n\n⚠️ No files were actually written — please try again or rephrase."
        )
        final_assistant_text += warning
        await on_event("warning", {"message": warning.strip()})

    # Auto-build React if __src__/ was touched but model didn't call build_react
    if tech_stack == "react" and src_touched and stats.builds == 0:
        ok, msg, built = await run_build_react(app_id_val, app_slug)
        if ok:
            files_written.extend(built)
            any_write_succeeded = True
            await on_event("build_complete", {"files": built, "auto": True})
        else:
            await on_event("warning", {"message": f"Auto-build failed: {msg[:500]}"})

    db3 = SessionLocal()
    try:
        app_row = db3.get(App, app_id_val)
        if app_row:
            import json
            app_row.chat_history = json.dumps(
                _compress_history(messages, file_edits=turn_file_edits or None),
            )
            _update_manifest(app_row, db3)
            if files_written:
                app_row.status = "deployed"
            db3.commit()
    finally:
        db3.close()

    _record_usage("lab", _MODEL, usage_acc)
    return BuilderResult(final_assistant_text, files_written, True, stats, src_touched)
