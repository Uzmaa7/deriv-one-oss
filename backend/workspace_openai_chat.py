"""
OpenAI /v1/chat/completions transport for the Workspace Auto router.

the model gateway's Anthropic /v1/messages passthrough breaks multi-turn Auto routing
(assistant history becomes output_text → HTTP 400 on turn 2+). The OpenAI
chat-completions path keeps history in a format all Auto tier backends accept.

Used only when the user selects Auto; every other canonical model stays on the
Anthropic SDK path in main.py.
"""
from __future__ import annotations

import asyncio
import json
import logging
from collections.abc import AsyncIterator, Awaitable, Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Optional

from openai import APIStatusError, AsyncOpenAI

from executor import execute_in_sandbox
from model_catalog import gateway_openai_base_url
from token_tracking import TokenAccumulator

logger = logging.getLogger(__name__)

# SSE payload dicts yielded to main.py (wrapped with _sse there).
SsePayload = dict[str, Any]

FetchUrlFn = Callable[[str], Awaitable[str]]
WorkspaceDirFn = Callable[[str], Path]
RewritePathsFn = Callable[[str, str], str]
RegisterPdfsFn = Callable[[Path, str, set[str]], None]
RegisterFileFn = Callable[[str, str], None]


@dataclass
class ChatToolRuntime:
    """Dependencies from main.py — avoids circular imports."""

    owner_sub: str
    chat_id: str
    merged_secrets: dict[str, str]
    fetch_url: FetchUrlFn
    user_workspace_dir: WorkspaceDirFn
    rewrite_paths: RewritePathsFn
    register_new_pdfs: RegisterPdfsFn
    register_generated_file: RegisterFileFn
    tool_audit: list[dict] = field(default_factory=list)
    skill_slugs_for_artifacts: list[str] = field(default_factory=list)


@dataclass
class OpenAIChatState:
    accumulated: str = ""
    thinking_accumulated: str = ""
    tool_errors: int = 0
    tool_call_count: int = 0  # total client-side tool calls this turn (for progress nudges)
    exit_mode: Optional[str] = None
    routed_model: Optional[str] = None
    # Final OpenAI-format message list after all tool turns.  Populated by
    # stream_workspace_auto_chat so Answer Now synthesis can see tool outputs.
    openai_messages: list[dict] = field(default_factory=list)


def anthropic_tools_to_openai(tools: list[dict]) -> list[dict]:
    """Convert Anthropic custom tool defs to OpenAI function tools."""
    out: list[dict] = []
    for tool in tools:
        if tool.get("type") in ("web_search_20260209", "web_fetch_20260209"):
            continue
        name = tool.get("name")
        if not name:
            continue
        out.append({
            "type": "function",
            "function": {
                "name": name,
                "description": tool.get("description", ""),
                "parameters": tool.get("input_schema", {"type": "object", "properties": {}}),
            },
        })
    return out


def _user_content_to_openai(content: Any) -> Any:
    if isinstance(content, str):
        return content
    if not isinstance(content, list):
        return str(content)

    openai_parts: list[dict] = []
    for block in content:
        if not isinstance(block, dict):
            continue
        btype = block.get("type")
        if btype == "text":
            openai_parts.append({"type": "text", "text": block.get("text", "")})
        elif btype == "image":
            src = block.get("source") or {}
            if src.get("type") == "base64":
                mime = src.get("media_type", "image/jpeg")
                data = src.get("data", "")
                openai_parts.append({
                    "type": "image_url",
                    "image_url": {"url": f"data:{mime};base64,{data}"},
                })
        elif btype == "document":
            openai_parts.append({
                "type": "text",
                "text": "[PDF document attached — summarised from upload metadata]",
            })
        elif btype == "tool_result":
            openai_parts.append({
                "type": "text",
                "text": str(block.get("content", "")),
            })

    if not openai_parts:
        return ""
    if len(openai_parts) == 1 and openai_parts[0].get("type") == "text":
        return openai_parts[0]["text"]
    return openai_parts


def _assistant_content_to_openai(content: Any) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        texts = [
            b.get("text", "")
            for b in content
            if isinstance(b, dict) and b.get("type") == "text"
        ]
        return "\n".join(t for t in texts if t).strip()
    return str(content)


def build_openai_messages(system: str, anthropic_messages: list[dict]) -> list[dict]:
    """Seed OpenAI message list from Hub DB history (Anthropic-shaped)."""
    messages: list[dict] = [{"role": "system", "content": system}]
    for msg in anthropic_messages:
        role = msg.get("role")
        content = msg.get("content")
        if role == "user":
            messages.append({"role": "user", "content": _user_content_to_openai(content)})
        elif role == "assistant":
            messages.append({
                "role": "assistant",
                "content": _assistant_content_to_openai(content),
            })
    return messages


def _usage_from_openai_response(usage: Any, acc: TokenAccumulator) -> None:
    if usage is None:
        return
    input_tokens = getattr(usage, "prompt_tokens", None) or getattr(usage, "input_tokens", 0)
    output_tokens = getattr(usage, "completion_tokens", None) or getattr(usage, "output_tokens", 0)

    class _Usage:
        pass

    u = _Usage()
    u.input_tokens = int(input_tokens or 0)
    u.output_tokens = int(output_tokens or 0)
    acc.add(u)


async def _execute_run_python(
    tool_call_id: str,
    arguments: dict,
    runtime: ChatToolRuntime,
    session_ctrl: Any,
) -> tuple[list[SsePayload], str, bool, bool]:
    """Run sandbox; return (sse_events, tool_result_text, ok, cancelled)."""
    import time as _time

    code = arguments.get("code", "")
    step_label = arguments.get("label", "Running code…")
    sse: list[SsePayload] = []
    work_dir = runtime.user_workspace_dir(runtime.owner_sub)
    patched_code = runtime.rewrite_paths(code, str(work_dir))
    before_pdf_set = {p.name for p in work_dir.glob("*.pdf")}
    t0 = _time.monotonic()

    sse.append({"type": "step_start", "id": tool_call_id, "label": step_label})
    sse.append({"type": "tool_start", "label": step_label})

    sandbox_task = asyncio.create_task(
        execute_in_sandbox(patched_code, str(work_dir), runtime.merged_secrets or None)
    )
    cancelled = False
    while not sandbox_task.done():
        if session_ctrl.event.is_set():
            sandbox_task.cancel()
            cancelled = True
            try:
                await sandbox_task
            except asyncio.CancelledError:
                pass
            break
        try:
            await asyncio.wait_for(asyncio.shield(sandbox_task), timeout=5.0)
        except asyncio.TimeoutError:
            sse.append({"type": "keepalive"})

    duration_ms = int((_time.monotonic() - t0) * 1000)
    if cancelled:
        sse.append({
            "type": "step_end",
            "id": tool_call_id,
            "ok": False,
            "duration_ms": duration_ms,
        })
        sse.append({"type": "tool_end", "ok": False})
        return sse, "(tool run stopped by user)", False, True

    exec_result = sandbox_task.result()
    runtime.register_new_pdfs(work_dir, runtime.owner_sub, before_pdf_set)
    runtime.tool_audit.append({
        "tool": "run_python",
        "code_len": len(code),
        "duration_ms": duration_ms,
        "ok": exec_result.get("exit_code", 1) == 0,
    })

    result_parts: list[str] = []
    if exec_result["stdout"]:
        result_parts.append(exec_result["stdout"].strip())
    if exec_result["stderr"]:
        result_parts.append(f"[stderr]\n{exec_result['stderr'].strip()}")
    result = "\n".join(result_parts) or "(no output)"
    ok = "[stderr]" not in result and not result.startswith("(no output)")

    sse.append({"type": "step_end", "id": tool_call_id, "ok": ok, "duration_ms": duration_ms, "output": result})
    sse.append({"type": "tool_end", "ok": ok})

    # Tool stdout stays on the tool-result message for the model — do NOT stream it
    # into the assistant chat bubble (that used to look like a raw "CODE" dump).

    for f in exec_result.get("files", []):
        fname = f["filename"]
        fsize = f["size"]
        fmime = f["mime"]
        runtime.register_generated_file(fname, runtime.owner_sub)
        file_url = f"/api/files/{fname}"
        local_path = work_dir / fname
        if local_path.is_file():
            sse.append({"type": "keepalive"})
            try:
                from providers import get_blob_store

                _key = f"generated/{runtime.owner_sub}/{fname}"
                _loop = asyncio.get_running_loop()
                await _loop.run_in_executor(
                    None,
                    lambda: get_blob_store().put(
                        _key, local_path.read_bytes(), content_type=fmime
                    ),
                )
            except Exception:
                logger.exception("storing generated file failed for %s", fname)

        sse.append({
            "type": "file_generated",
            "filename": fname,
            "url": file_url,
            "mime": fmime,
            "size_bytes": fsize,
        })
        marker = f"\n<!-- GENFILE:{json.dumps({'f': fname, 'u': file_url, 'm': fmime, 's': fsize})} -->"
        sse.append({"type": "text", "content": marker})

    return sse, result, ok, False


async def _execute_call_artifact_tool(
    tool_call_id: str,
    arguments: dict,
    runtime: ChatToolRuntime,
    session_ctrl: Any,
) -> tuple[list[SsePayload], str, bool, bool]:
    from api_slack_events import _execute_call_artifact

    name = str(arguments.get("name", "")).strip()
    params = arguments.get("params")
    if not isinstance(params, dict):
        params = {}
    label = f"Artifact {name or '…'}"
    sse: list[SsePayload] = [
        {"type": "step_start", "id": tool_call_id, "label": label},
        {"type": "tool_start", "label": label},
    ]
    if session_ctrl.event.is_set():
        sse.append({"type": "step_end", "id": tool_call_id, "ok": False, "duration_ms": 0})
        sse.append({"type": "tool_end", "ok": False})
        return sse, "(tool run stopped by user)", False, True

    output = await _execute_call_artifact(
        name,
        params,
        accessible_skill_slugs=runtime.skill_slugs_for_artifacts,
        skill_secrets=runtime.merged_secrets or {},
        agent_id=f"workspace:{runtime.owner_sub}",
        tool_audit=runtime.tool_audit,
    )
    ok = not output.startswith("[error]")
    sse.append({"type": "step_end", "id": tool_call_id, "ok": ok, "duration_ms": 0, "output": output})
    sse.append({"type": "tool_end", "ok": ok})
    return sse, output, ok, False


def _execute_propose_tool(
    tool_name: str, arguments: dict, runtime: ChatToolRuntime,
) -> tuple[list[SsePayload], str]:
    """Mirrors main.py's Claude-direct propose_* dispatch branch — see
    resource_proposals.py for the shared marker/content-shape logic."""
    from resource_proposals import content_for_kind, create_resource_proposal, proposal_marker

    kind = tool_name.removeprefix("propose_")
    name = str(arguments.get("name", "Untitled")).strip()[:300] or "Untitled"
    content = content_for_kind(kind, name, arguments)

    proposal = create_resource_proposal(
        chat_id=runtime.chat_id, owner_sub=runtime.owner_sub, kind=kind, name=name, content=content,
    )
    marker = proposal_marker(proposal.id, kind, name)
    events: list[SsePayload] = [
        {"type": "proposal_created", "proposal_id": proposal.id, "kind": kind, "name": name},
        {"type": "text", "content": marker},
    ]
    result_text = (
        f"Draft {kind} \"{name}\" created and shown to the user for review "
        f"(proposal_id={proposal.id}). Do not call propose_{kind} again for this "
        "request — wait for the user's decision."
    )
    return events, result_text


_MAX_LOCAL_TOOL_RESULT_CHARS = 1_500


def _truncate_for_local(content: str, limit: int = _MAX_LOCAL_TOOL_RESULT_CHARS) -> str:
    if len(content) <= limit:
        return content
    return content[:limit] + "\n\n[output truncated for local model context]"


async def stream_workspace_auto_chat(
    *,
    model: str,
    system: str,
    initial_messages: list[dict],
    tools: list[dict],
    api_key: str,
    base_url_raw: str,
    runtime: ChatToolRuntime,
    session_ctrl: Any,
    usage_acc: TokenAccumulator,
    state: OpenAIChatState,
    max_tool_turns: int = 25,
    max_tokens: int = 8192,
    local_context_limit: Optional[int] = None,
) -> AsyncIterator[SsePayload]:
    """
    Stream a Workspace chat turn using OpenAI chat/completions via model gateway.

    Yields SSE payload dicts. Mutates ``state`` (accumulated text, tool errors).
    Raises ``APIStatusError`` on unrecoverable API failures.
    """
    client = AsyncOpenAI(
        api_key=api_key,
        base_url=gateway_openai_base_url(base_url_raw),
    )
    openai_tools = anthropic_tools_to_openai(tools) if tools else None
    messages = build_openai_messages(system, initial_messages)
    last_progress_nudge_count = 0
    tools_json_chars = len(json.dumps(openai_tools or []))

    for turn in range(max_tool_turns):
        if session_ctrl.event.is_set():
            state.exit_mode = session_ctrl.mode
            break

        turn_max_tokens = max_tokens
        if local_context_limit is not None:
            from model_gateway import (
                compact_openai_messages_for_local,
                local_gateway_max_output_tokens,
            )

            messages = compact_openai_messages_for_local(messages)
            turn_max_tokens = local_gateway_max_output_tokens(
                system,
                messages,
                context_limit=local_context_limit,
                tools_json_chars=tools_json_chars,
            )

        print(
            f"[CHAT/openai] stream start model={model} turn={turn} msgs={len(messages)} "
            f"max_tokens={turn_max_tokens}",
            flush=True,
        )

        tool_calls_acc: dict[int, dict] = {}
        finish_reason: Optional[str] = None
        chunk_model: Optional[str] = None
        stream_text = ""

        stream = await client.chat.completions.create(
            model=model,
            messages=messages,
            max_tokens=turn_max_tokens,
            tools=openai_tools if openai_tools else None,
            stream=True,
            stream_options={"include_usage": True},
        )

        async for chunk in stream:
            if session_ctrl.event.is_set():
                state.exit_mode = session_ctrl.mode
                break

            if chunk.model:
                chunk_model = chunk.model
            if chunk.usage:
                _usage_from_openai_response(chunk.usage, usage_acc)

            if not chunk.choices:
                continue
            choice = chunk.choices[0]
            if choice.finish_reason:
                finish_reason = choice.finish_reason
            delta = choice.delta
            if delta is None:
                continue

            if delta.content:
                stream_text += delta.content
                state.accumulated += delta.content
                yield {"type": "text", "content": delta.content}

            if delta.tool_calls:
                for tc in delta.tool_calls:
                    idx = tc.index
                    if idx not in tool_calls_acc:
                        tool_calls_acc[idx] = {
                            "id": tc.id or "",
                            "name": tc.function.name if tc.function else "",
                            "arguments": "",
                        }
                    if tc.id:
                        tool_calls_acc[idx]["id"] = tc.id
                    if tc.function:
                        if tc.function.name:
                            tool_calls_acc[idx]["name"] = tc.function.name
                        if tc.function.arguments:
                            tool_calls_acc[idx]["arguments"] += tc.function.arguments

        if state.exit_mode:
            break

        if chunk_model:
            state.routed_model = chunk_model

        print(
            f"[CHAT/openai] stream end model={model} finish={finish_reason} "
            f"accumulated={len(state.accumulated)} tool_calls={len(tool_calls_acc)}",
            flush=True,
        )

        if not tool_calls_acc or not openai_tools:
            break

        # Append assistant message with tool calls for the next round.
        assistant_msg: dict[str, Any] = {
            "role": "assistant",
            "content": stream_text or None,
            "tool_calls": [],
        }
        for idx in sorted(tool_calls_acc):
            entry = tool_calls_acc[idx]
            assistant_msg["tool_calls"].append({
                "id": entry["id"],
                "type": "function",
                "function": {
                    "name": entry["name"],
                    "arguments": entry["arguments"],
                },
            })
        messages.append(assistant_msg)

        for idx in sorted(tool_calls_acc):
            if state.exit_mode:
                break
            entry = tool_calls_acc[idx]
            name = entry["name"]
            try:
                args = json.loads(entry["arguments"] or "{}")
            except json.JSONDecodeError:
                args = {}

            if name == "run_python":
                events, result, ok, cancelled = await _execute_run_python(
                    entry["id"], args, runtime, session_ctrl,
                )
                for ev in events:
                    if ev.get("type") == "keepalive":
                        yield {"type": "__keepalive__"}
                    elif ev.get("type") == "text":
                        state.accumulated += ev.get("content", "")
                        yield ev
                    else:
                        yield ev
                if cancelled:
                    state.exit_mode = session_ctrl.mode
                    messages.append({
                        "role": "tool",
                        "tool_call_id": entry["id"],
                        "content": "(tool run stopped by user)",
                    })
                    break
                if not ok:
                    state.tool_errors += 1
                else:
                    state.tool_errors = 0
                state.tool_call_count += 1
                tool_content = (
                    _truncate_for_local(result)
                    if local_context_limit is not None
                    else result
                )
                messages.append({
                    "role": "tool",
                    "tool_call_id": entry["id"],
                    "content": tool_content,
                })

            elif name == "call_artifact" and runtime.skill_slugs_for_artifacts:
                events, result, ok, cancelled = await _execute_call_artifact_tool(
                    entry["id"], args, runtime, session_ctrl,
                )
                for ev in events:
                    if ev.get("type") == "keepalive":
                        yield {"type": "__keepalive__"}
                    elif ev.get("type") == "text":
                        state.accumulated += ev.get("content", "")
                        yield ev
                    else:
                        yield ev
                if cancelled:
                    state.exit_mode = session_ctrl.mode
                    messages.append({
                        "role": "tool",
                        "tool_call_id": entry["id"],
                        "content": "(tool run stopped by user)",
                    })
                    break
                if not ok:
                    state.tool_errors += 1
                else:
                    state.tool_errors = 0
                tool_content = (
                    _truncate_for_local(result)
                    if local_context_limit is not None
                    else result
                )
                messages.append({
                    "role": "tool",
                    "tool_call_id": entry["id"],
                    "content": tool_content,
                })

            elif name == "fetch_url":
                url = args.get("url", "")
                yield {"type": "tool_start", "label": f"Fetching {url[:60]}…"}
                fetch_result = await runtime.fetch_url(url)
                ok = not fetch_result.startswith("Error:")
                yield {"type": "tool_end", "ok": ok}
                if not ok:
                    state.tool_errors += 1
                else:
                    state.tool_errors = 0
                state.tool_call_count += 1
                messages.append({
                    "role": "tool",
                    "tool_call_id": entry["id"],
                    "content": fetch_result,
                })

            elif name in ("propose_skill", "propose_automation", "propose_lab"):
                events, result = _execute_propose_tool(name, args, runtime)
                for ev in events:
                    if ev.get("type") == "text":
                        state.accumulated += ev.get("content", "")
                    yield ev
                state.tool_call_count += 1
                state.tool_errors = 0
                messages.append({
                    "role": "tool",
                    "tool_call_id": entry["id"],
                    "content": result,
                })

        # Every 3 tool calls, nudge the model to post a brief progress update
        # (what's been tried / what's next) before it keeps going, so long
        # tool-call chains don't leave the user without feedback.
        if tool_calls_acc and state.tool_call_count - last_progress_nudge_count >= 3:
            last_progress_nudge_count = state.tool_call_count
            messages.append({
                "role": "user",
                "content": (
                    "[SYSTEM NOTE — not from the user] You've made several tool calls. "
                    "Before continuing, briefly update the user (2-4 sentences) on what "
                    "you've tried so far and what you still plan to try next, then "
                    "continue the task."
                ),
            })

        if state.tool_errors >= 5:
            err_text = (
                "\n\n> ⚠️ The query could not be executed — the tool returned errors "
                "on multiple attempts. Please check that the required credentials are configured."
            )
            state.accumulated += err_text
            yield {"type": "text", "content": err_text}
            break

        if finish_reason != "tool_calls" and not tool_calls_acc:
            break

    # Expose the final OpenAI-format message list (including all tool turns) so
    # that Answer Now synthesis can see the actual outputs from this turn.
    state.openai_messages = messages

    await client.close()


async def stream_auto_answer_now_synthesis(
    *,
    model: str,
    system: str,
    messages: list[dict],
    api_key: str,
    base_url_raw: str,
    usage_acc: TokenAccumulator,
    state: OpenAIChatState,
    session_ctrl: Any,
    max_tokens: int = 4096,
) -> AsyncIterator[SsePayload]:
    """Answer-now synthesis via OpenAI path (no tools).

    Uses ``state.openai_messages`` when available (populated by
    ``stream_workspace_auto_chat`` after all tool turns), so the synthesis
    model sees the actual tool outputs from this turn.  Falls back to
    converting the Anthropic-shaped ``messages`` for robustness.
    """
    yield {"type": "answer_now", "content": "Synthesizing answer from data collected so far…"}

    client = AsyncOpenAI(
        api_key=api_key,
        base_url=gateway_openai_base_url(base_url_raw),
    )
    # Prefer the live OpenAI-format list written back by stream_workspace_auto_chat —
    # it contains assistant messages, tool call entries, and tool result messages.
    # build_openai_messages() only converts user/assistant turns and would lose all
    # tool outputs if used on the raw Anthropic-shaped history.
    if state.openai_messages:
        openai_messages = list(state.openai_messages)
    else:
        openai_messages = build_openai_messages(system, messages)
    openai_messages.append({
        "role": "user",
        "content": (
            "The user clicked **Answer now**. Do not call any tools. "
            "Provide the best possible answer using only information already "
            "available in this conversation (including prior tool outputs). "
            "If something could not be retrieved, state what is missing."
        ),
    })

    stream = await client.chat.completions.create(
        model=model,
        messages=openai_messages,
        max_tokens=max_tokens,
        stream=True,
        stream_options={"include_usage": True},
    )
    async for chunk in stream:
        if session_ctrl.event.is_set() and session_ctrl.mode == "stop":
            break
        if chunk.usage:
            _usage_from_openai_response(chunk.usage, usage_acc)
        if not chunk.choices:
            continue
        delta = chunk.choices[0].delta
        if delta and delta.content:
            state.accumulated += delta.content
            yield {"type": "text", "content": delta.content}

    await client.close()
