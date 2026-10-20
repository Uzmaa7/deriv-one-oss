# Runbook: Agent memory export (compliance / migration)

Agent memory lives in the database (`agent_memories`) and, for newer deployments,
in the **blob-backed episode archive** under `memory_store/`. Compliance or
migration projects may need a one-off export of legacy rows to durable storage.

This repository does **not** ship an export script. Build export tooling in your
private ops environment with direct access to Postgres and your `BLOB_STORE`.

## What to export

| Source | Contents |
|--------|----------|
| `agent_memories` table | Legacy memory rows per agent |
| Blob archive (`agent-memory/agents/<id>/…`) | Markdown episodes + manifest (if enabled) |

Use `agents.memory_to_dict()` and `memory_store` types as reference for field
shapes — read from a maintenance shell or one-off job running the backend image.

## Suggested layout (GCS/S3)

```
agent-memory/agents/<agent_id>/migration/<run_id>/manifest.json
agent-memory/agents/<agent_id>/migration/<run_id>/episodes/<memory_id>.md
agent-memory/agents/<agent_id>/MEMORY.md
```

## Safety

- Run exports from a **read-only** DB role where possible.
- Redact or exclude other agents' data — scope by `agent_id` and tenancy (`owner_sub`).
- Do not commit exported content to git.
- Log export `run_id` and row counts only; not memory body text.

## Related

- `backend/memory_store/` — consolidation, policy, archive
- `backend/agents.py` — `AgentMemory` model
- [ADOPTER_GUIDE.md](../ADOPTER_GUIDE.md) — persistence and `BLOB_STORE`
