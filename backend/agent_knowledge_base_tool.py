"""The ``search_knowledge_base`` tool offered to knowledge-base-enabled agents.

Defined once in Anthropic shape with a derived OpenAI variant, matching the
convention used by ``run_python`` / ``call_artifact`` / ``activate_skill`` in
``api_slack_events.py`` — this platform runs agents on both the Anthropic SDK and
OpenAI-transport (the model gateway), so every tool needs both shapes and one handler.

Security note: the tool takes **only** a query string. The data store is resolved
server-side from the agent's own row. Deliberately *not* a parameter, because a
model-supplied data store ID would let one agent read another agent's documents —
the per-agent isolation boundary is enforced here, not by the model's good
behaviour. See AGENT_KNOWLEDGE_BASE_SPEC.md §5.2.
"""

from __future__ import annotations

import logging

logger = logging.getLogger(__name__)

SEARCH_KB_TOOL = {
    "name": "search_knowledge_base",
    "description": (
        "Search this agent's knowledge base of uploaded documents. "
        "Call this whenever the user's question might be answered by the "
        "documents attached to this agent — for example questions about "
        "internal policies, specifications, or reference material. "
        "Returns relevant excerpts with their source filenames; cite the "
        "filename when you use an excerpt. If the result is empty, say the "
        "knowledge base has nothing on the topic rather than guessing."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "query": {
                "type": "string",
                "description": (
                    "Natural-language search query. Use the user's own wording "
                    "where possible; retry with different phrasing if the first "
                    "search returns nothing useful."
                ),
            }
        },
        "required": ["query"],
    },
}

SEARCH_KB_TOOL_OPENAI = {
    "type": "function",
    "function": {
        "name": "search_knowledge_base",
        "description": SEARCH_KB_TOOL["description"],
        "parameters": SEARCH_KB_TOOL["input_schema"],
    },
}

# Cap the number of excerpts returned to the model. Retrieval quality drops off
# quickly past the top handful, and each excerpt costs context.
_MAX_EXCERPTS = 6


def knowledge_base_enabled_for_agent(agent) -> bool:
    """Whether ``search_knowledge_base`` should be offered for this agent."""
    try:
        import agent_knowledge_base_service as kb_service
        from database import SessionLocal

        db = SessionLocal()
        try:
            return kb_service.get_usable_kb(db, str(agent.id)) is not None
        finally:
            db.close()
    except Exception:  # noqa: BLE001 — a lookup failure must not break the turn
        logger.exception("kb_tool: enablement check failed for agent %s", getattr(agent, "id", "?"))
        return False


def execute_search_knowledge_base(agent_id: str, query: str, agent_name: str | None = None) -> str:
    """Run the search and format results for the model.

    Returns a plain-text tool result. An empty knowledge-base hit is reported as
    a normal outcome, not an error, so the model states the gap rather than
    fabricating an answer.
    """
    import agent_knowledge_base_service as kb_service

    query = (query or "").strip()
    if not query:
        return "[error] search_knowledge_base requires a non-empty query."

    try:
        results = kb_service.search_for_agent(agent_id, query, agent_name)
    except Exception:  # noqa: BLE001
        logger.exception("kb_tool: search failed for agent %s", agent_id)
        return (
            "[error] The knowledge base could not be searched right now. "
            "Answer from your own knowledge and say the documents were unavailable."
        )

    if not results:
        return (
            f"No documents in this agent's knowledge base matched {query!r}. "
            f"Tell the user the knowledge base has nothing on this topic — do not "
            f"invent a source."
        )

    lines: list[str] = []
    for i, item in enumerate(results[:_MAX_EXCERPTS], start=1):
        lines.append(f"[{i}] {item['filename']}\n{item['snippet']}")
    return "\n\n".join(lines)


def append_kb_audit(tool_audit: list | None, query: str, result: str) -> None:
    """Record the call in the turn's tool audit trail.

    Only the query and a result size are recorded — document excerpts may contain
    sensitive content and are never written to the audit log.
    """
    if tool_audit is None:
        return
    tool_audit.append({
        "tool": "search_knowledge_base",
        "query": query[:500],
        "result_chars": len(result or ""),
        "ok": not (result or "").startswith("[error]"),
    })
