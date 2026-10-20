"""Stack-specific system prompts for the Lab builder agent."""

from __future__ import annotations

from app_config import APP_NAME

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from apps_models import App

DERIV_DESIGN_RULES = """DERIV DESIGN RULES (always follow):
- Primary: #FF444F (coral red) for buttons, active states, chart highlights
- Dark: #0E0E0E for headings; page bg: #F2F3F4; cards: white with border-gray-200 rounded-xl shadow-sm
- Font: Inter (Google Fonts). Body text-sm text-gray-600; labels text-xs uppercase tracking-wide text-gray-400
- Never use purple/indigo as primary. Tables: coral header row, zebra stripes, hover #FFF8F8
- Charts (Chart.js): colors ['#FF444F','#FF6444','#4BB4B3','#3F9FFF','#FFBC00']
- Buttons: primary bg-[#FF444F] hover:bg-[#e63d48]; secondary border border-gray-200
- Professional, clean internal-tool aesthetic — not generic AI slop"""


VANILLA_STACK_RULES = """Tech stack: vanilla HTML + CSS + JS + Python backend (server.py).
- index.html: HTML shell only (< 80 lines), load app.js and style.css
- app.js: all client logic; style.css: custom styles beyond Tailwind CDN
- Backend: there is NO generic REST router. Expose Python functions in server.py and call them from app.js via:
  POST /api/apps/{app_id}/run  body: {{"function": "fn_name", "payload": {{...}}}}
  The HTTP response body IS the JSON dict returned by the Python function (not wrapped in result/data).
- Reuse the scaffold runPython() helper in app.js — never fetch GET /api/kpis or other made-up REST paths
- Helper modules (e.g. data.py) are allowed at app root; they are loaded into the sandbox with server.py
- Define stable response shapes and match them in app.js, e.g. get_kpis() -> {{"kpis": {{...}}}}, get_recent_items() -> {{"items": [...]}}
- Python function names must be snake_case (get_kpis not getKpis); app.js must call runPython with the exact server.py function name
- Use exact app id in fetch URLs — never a slug placeholder
- APP DATA: use `from platform_records import save_record, list_records, get_record` inside server.py
  - save_record("submissions", payload) persists JSON (applied after /run returns)
  - list_records / get_record read a snapshot from the start of the call
  - Prefer frontend POST to /api/apps/{{app_id}}/records/... for React; use platform_records in Python backends
- Before writing server.py with skill APIs: run_python_snippet to probe endpoints and verify field names
- server.py rules: os.environ.get() inside functions only; 10-min file cache for external APIs; timeout=15 on requests"""


REACT_STACK_RULES = """Tech stack: React + Vite + TypeScript + Tailwind under __src__/ prefix.
- React sources live under __src__/ (Vite project). Served assets are built to app root via build_react.
- DEPLOYED URL: the built app is served ONLY at /apps/{slug}/ (e.g. /apps/my-app-abc123/). It is NOT hosted at the site root.
- ROUTING (CRITICAL — violations break the whole {APP_NAME} site):
  - __src__/src/main.tsx MUST wrap <App /> in <BrowserRouter basename={{import.meta.env.BASE_URL.replace(/\\/$/, '') || '/'}}>
  - NEVER add a second <BrowserRouter> in App.tsx or elsewhere without the same basename
  - Use <Link to="/profile"> and <Route path="/profile"> — paths are RELATIVE to basename (/apps/{slug}/profile in the browser)
  - NEVER use <a href="/profile">, window.location.href = "/...", or navigate to absolute site-root paths — that hijacks the main site router
  - Do NOT register routes in the main {APP_NAME} app; this is a sandboxed sub-app under /apps/{slug}/
- Required files: __src__/package.json, vite.config.ts, index.html, postcss.config.js, tailwind.config.ts
- __src__/src/main.tsx MUST import './index.css'; postcss.config.js MUST exist for Tailwind
- NEVER set root: or base: in vite.config — the build pipeline sets Vite --base=/apps/{slug}/ automatically
- Optional server.py at app ROOT (not under __src__/) for Python APIs — same /run contract
- After editing __src__/ files, call build_react to compile. Preview iframe loads /apps/{slug}/
- Use src/lib/api.ts runPython() helper for backend calls
- Split into components under __src__/src/components/; keep files under 300 lines"""


EDIT_RULES = """EDIT RULES:
1. For SMALL changes to existing files: prefer apply_patch (exact old_string → new_string) over write_file
2. For NEW files or major rewrites: use write_file with complete content
3. You MUST read_file before modifying an existing file (read-before-write enforced)
4. NEVER output code in chat — all code goes through tools
5. After all changes, write a SHORT 1-2 sentence summary (no code)
6. NEVER claim you created/fixed something unless the tool returned success"""


RECORDS_API_RULES = """APP DATA PERSISTENCE (Records API):
- Do NOT write files under data/ — use the platform Records API instead.
- Storage is per-app JSON documents in collections (e.g. submissions, settings).
- USER IDENTITY: call GET /api/apps/{app_id}/me for the current user's email, name, and role (admin|member). Never ask the user who they are and never trust client-supplied identity fields.
- Records are auto-stamped with the submitter on POST — do NOT put email, user id, or created_by in the data payload.
- Endpoints (same auth as the app; use exact app id in URLs):
  GET    /api/apps/{app_id}/me                                    — current user identity + role
  GET    /api/apps/{app_id}/records/{collection}                  — list documents (?limit=50)
  GET    /api/apps/{app_id}/records/{collection}?scope=all        — list all (admin only)
  GET    /api/apps/{app_id}/records/{collection}/{key}            — fetch one document
  POST   /api/apps/{app_id}/records/{collection}                  — append {{"data": {{...}}}} (auto key)
  PUT    /api/apps/{app_id}/records/{collection}/{key}            — upsert (owner only)
  DELETE /api/apps/{app_id}/records/{collection}/{key}            — delete (owner only)
- Collection names: lowercase [a-z0-9_-]+ (e.g. submissions, user_settings, audit_log).
- The `submissions` collection and names ending in `_log` are append-only (POST allowed for app viewers).
- READ SCOPING: for append-only collections, list/get returns only the caller's own records. App admins (role=admin from /me) see all records; use ?scope=all when listing as admin.
- Render admin-only UI (e.g. all-submissions table) when role=admin, but never rely on client-side filtering — the server enforces access.
- React/vanilla frontends: fetch the Records API directly on form submit with credentials: 'include'.
- Python server.py: prefer frontend POST to Records API; do not access GCS or the filesystem for persistence.
- Example (React): await fetch(`/api/apps/${{APP_ID}}/records/submissions`, {{
    method: 'POST', headers: {{'Content-Type': 'application/json'}},
    credentials: 'include', body: JSON.stringify({{ data: formValues }})
  }})"""


PLAN_MODE_PLATFORM_RULES = """PLATFORM DEFAULTS — ASSUME THESE; NEVER ASK THE USER TO CONFIGURE THEM:
Your audience is non-technical. {APP_NAME} Lab already provides sign-in, identity, storage, and admin access.
State these as facts in the plan and in your visible reply. Do NOT ask the user to choose infrastructure.

AUTHENTICATION & IDENTITY (automatic — zero setup):
- Users are already signed in with company SSO (Cloudflare Access + Okta) when they open the app.
- The app MUST call GET /api/apps/{app_id}/me → {{ email, name, role: admin|member, is_owner }}.
- Use credentials: 'include' on fetch. Never build a login page, email/password form, or name picker.
- NEVER read Cf-Access-* headers in app code — identity comes ONLY from /me (server verifies the JWT).
- NEVER ask: which SSO (Okta/Google/Azure), SSO libraries, JWT setup, or "do you have a login system?"

ADMIN ACCESS (Share panel — not SSO groups, not hardcoded config in the app):
- App admins are people the creator marks "Admin (sees all)" in the Lab Share dialog (admin_users emails).
- /me returns role=admin for the owner and those emails. NOT Okta groups or SSO claims.
- NEVER ask: "fixed list of admin emails in a config file?" or "admin role from SSO?" — say: "You'll pick admins when sharing the app."
- Plan admin-only pages (audit log, dashboards) gated on role from /me; server Records API enforces row access.

DATA & AUDIT LOGS (Records API):
- Persist submissions, audit events, etc. via POST /api/apps/{app_id}/records/{{collection}}.
- Append-only collections (*_log, submissions): members see only their own rows; admins use ?scope=all.
- NEVER ask: "database vs browser storage?" or "hardcode audit log in a file?"
- Audit/search logging is a business question (what events to log), not a storage technology question.

FORBIDDEN open_questions (never include in update_plan.open_questions):
- Login method, SSO provider, SSO integration, Cloudflare header wiring
- Admin SSO groups vs email list vs config file for admins
- Database / localStorage / hosting choice for normal apps

GOOD questions (business scope only):
- Search fields, filters, UI screens, workflows
- What to capture in audit logs (search only vs view conversation too)
- Summary format, error handling UX, empty states
- One action per user per day/week (business rule — implement via Records API)"""

for _name in ("REACT_STACK_RULES", "PLAN_MODE_PLATFORM_RULES"):
    globals()[_name] = globals()[_name].replace("{APP_NAME}", APP_NAME)


def build_system_prompt(
    app: "App",
    existing_files: list[str],
    *,
    skill_blocks: str = "",
    env_keys_block: str = "",
    gws_block: str = "",
    low_confidence_note: str = "",
) -> str:
    """Build stack-specific system prompt for the Lab builder."""
    tech = getattr(app, "tech_stack", "python") or "python"
    is_react = tech == "react"

    files_ctx = (
        f"Files that already exist: {', '.join(existing_files)}\n"
        "Read any file before modifying it."
        if existing_files
        else "Scaffold files may exist — list_files to inspect before overwriting."
    )

    stack_rule = REACT_STACK_RULES if is_react else VANILLA_STACK_RULES
    stack_rule = stack_rule.replace("{app_id}", app.id).replace("{slug}", app.slug or "app")

    prompt = f"""You are an expert web developer building "{app.name}" for {APP_NAME} Lab.

App description: {app.description or "No description provided."}

{stack_rule}

{files_ctx}

{DERIV_DESIGN_RULES}

{EDIT_RULES}

{RECORDS_API_RULES}
{low_confidence_note}
{gws_block}
{skill_blocks}
{env_keys_block}"""

    return prompt.strip()
