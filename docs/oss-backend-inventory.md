# OSS backend inventory — legal & engineering review

**Generated:** 2026-09-24  
**Purpose:** File-by-file disposition for the public open-source release of Deriv One / Platform.  
**Machine-readable:** [oss-backend-inventory.csv](./oss-backend-inventory.csv)

## Disposition legend

| Code | Meaning |
|------|---------|
| **KEEP** | Ship in the public MIT repository — required or intentional for adopters |
| **CUT** | Do **not** ship — keep in Deriv private fork or delete |
| **MOVE** | Relocate to private ops repo, `docs/runbooks/`, or adopters' own tooling |
| **REVIEW** | Engineering + legal decision — optional for v1 OSS |
| **GITIGNORE** | Must never appear in git (runtime secrets, local DB, build output) |

## Summary (current tree)

| Disposition | Count | Notes |
|-------------|------:|-------|
| KEEP | ~218 | Full product surface (workspace, skills, agents, Slack, Lab, setup) |
| CUT | 7 | **Overnight stack removed in P0** (see below) |
| MOVED | 2 | Ops scripts → `docs/runbooks/` (done 2026-09-24) |
| REVIEW | 5 | `warm_worker` (**KEEP** — documented as optional), `api_infra`, `mock_feedback`, optional requirement files |
| GITIGNORE | 5+ | `.env`, `platform.env`, `hub.db`, `var/`, `generated/` |

## P0 completed — Overnight stack (CUT)

These files were **removed from the OSS tree** on 2026-09-24. Shared LLM helpers moved to `backend/llm_json.py`.

| Path | Action |
|------|--------|
| `backend/api_overnight.py` | **Deleted** |
| `backend/overnight_common.py` | **Deleted** → replaced by `llm_json.py` |
| `backend/overnight_loop.py` | **Deleted** |
| `backend/overnight_models.py` | **Deleted** |
| `backend/overnight_verify.py` | **Deleted** |
| `backend/overnight_worker.py` | **Deleted** |
| `src/pages/OvernightPage.tsx` | **Deleted** |
| `docker-compose.workers.yml` | `overnight-worker` service **removed** |
| `src/App.tsx` | `/lab/:id/overnight` → redirect to `/lab/:id` (kept for bookmarks) |

**Database note:** Alembic migrations may still create `app_overnight_*` tables on existing DBs. Tables are inert without code. A future migration can drop them if desired.

## Recommended REVIEW items

| Path | Recommendation | If CUT |
|------|----------------|--------|
| `backend/warm_worker.py` | **KEEP** — optional advanced Slack consumer; README documents `slack_consumer_worker` as default |
| `backend/api_infra.py` | **KEEP** — harmless off-cluster; useful for K8s adopters | Remove admin infra tab |
| `backend/mock_feedback.py` | **KEEP** — improves mock-auth demo UX | Remove mock feedback API routes in `main.py` |
| `backend/requirements-gcp.txt` | **KEEP** as optional dep list | Move to `providers_reference/` only |
| `backend/requirements-k8s.txt` | **KEEP** as optional dep list | Same |
| `backend/requirements-integrations.txt` | **KEEP** | Same |

## MOVED — ops scripts (completed)

| Was | Now |
|-----|-----|
| `backend/scripts/slack_token_rotator.py` | **Deleted** → [runbooks/slack-token-rotation.md](./runbooks/slack-token-rotation.md) |
| `backend/scripts/export_agent_memory_archive.py` | **Deleted** → [runbooks/agent-memory-export.md](./runbooks/agent-memory-export.md) |

Implement rotation/export in your private ops repo using these runbooks as specs.

## Architecture map (why so many files?)

The backend is **one monolith** with a flat `api_*.py` layout — not 133 microservices.

```
backend/
├── main.py                 # App assembly + workspace routes (~4k lines)
├── api_*.py (33 files)     # HTTP routers (~32k lines total)
│   └── api_slack_events.py # Largest single file (~6.4k lines)
├── agents / slack / skills / lab modules
├── providers/              # Pluggable infrastructure (KEEP — OSS contract)
├── memory_store/           # Agent memory (KEEP)
├── workers (*_worker.py)   # Separate processes (KEEP)
├── alembic/                # Migrations (KEEP)
└── tests/                  # Unit tests (KEEP)
```

## Category reference

### KEEP — Core product (do not cut without dropping features)

| Category | Representative files | OSS feature |
|----------|---------------------|-------------|
| **core** | `main.py`, `database.py`, `secret_vault.py`, `executor.py` | Boot, DB, secrets, sandbox execution |
| **workspace** | `workspace_openai_chat.py`, `complexity_router.py`, `model_gateway.py` | Multi-pane chat, model routing |
| **skills** | `api_skill_publish.py`, `skills_acl.py`, `skill_router.py`, `artifact_gate.py` | Governed skills catalogue |
| **agents** | `api_agents.py`, `agents.py`, `agent_worker.py`, `memory_store/*` | Agent registration & runtime |
| **slack** | `api_slack_events.py`, `slack_oauth.py`, `slack_consumer_worker.py` | Slack agents (99% of Deriv traffic) |
| **lab** | `api_apps.py`, `lab_*.py`, `app_classifier.py`, `llm_json.py`, `lab_scaffolds/` | App builder |
| **workers** | `scheduler_worker.py`, `slack_consumer_worker.py`, `kb_ingest_worker.py` | Cron + Slack + KB indexing |
| **providers** | `providers/*.py` | Self-host / cloud plugin model |
| **auth** | `cf_access.py`, `api_access.py`, `access_models.py` | OIDC + governed access |
| **workflows** | `api_workflows.py`, `workflow_engine.py` | Agent workflows (beta) |
| **flows** | `api_flows.py` | User skill chains (beta) |
| **migrations** | `alembic/**` | Postgres adopters |
| **tests** | `tests/test_*.py` | CI credibility |
| **config** | `.env.example`, `platform.env.example`, `requirements.txt`, `Dockerfile` | Adopter setup |

### GITIGNORE — enforce in CI

Add to `.gitignore` if not already present:

```
backend/generated/
backend/var/
backend/hub.db
backend/.env
backend/platform.env
```

## Legal review checklist

- [x] No live secrets in tree (`scripts/check-forbidden-patterns.sh` in CI)
- [x] No Deriv-internal hostnames or employee PII in sample data
- [x] `mock_feedback.py` sample emails are fictional (`@example.com`)
- [x] Overnight CUT — no internal autonomous-build prompts in public tree
- [x] Third-party licences in `LICENSE` + `THIRD_PARTY_NOTICES.md`
- [ ] Patents / export-control review on Slack + sandbox execution docs (Deriv Legal — see [oss-release-signoff.md](./oss-release-signoff.md))

## Engineering review checklist

- [x] P0 overnight removal merged; `npm run lint` + backend tests green
- [x] `python -c "import main"` passes (FastAPI 204 routes fixed)
- [x] README / ADOPTER_GUIDE / cloud-deployment no longer reference overnight worker
- [x] `warm_worker.py` — **KEEP** (optional; documented in README / ADOPTER_GUIDE)
- [x] Ops scripts — **MOVED** to `docs/runbooks/`
- [ ] Post-launch: package refactor (`backend/agents/`, `backend/skills/`, …) — cosmetic only

## Related docs

- [ADOPTER_GUIDE.md](./ADOPTER_GUIDE.md)
- [cloud-deployment.md](./cloud-deployment.md)
- [slack-dmz-relay.md](./slack-dmz-relay.md)
- [blog-open-source-release.md](./blog-open-source-release.md)
