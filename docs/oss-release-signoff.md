# OSS release sign-off

Pre-publication audit for the **Deriv One** open-source release (`deriv-com/deriv-one-oss`).
Completed **2026-09-28**.

## Engineering

| Check | Status | Evidence |
|-------|--------|----------|
| Forbidden-pattern CI | Pass | `scripts/check-forbidden-patterns.sh` |
| TypeScript lint | Pass | `npm run lint` |
| Python tests | Pass | `pytest tests/` (70 tests) |
| `import main` smoke | Pass | `python -c "import main"` |
| Overnight stack removed | Done | P0 deletion + `llm_json.py` extraction |
| Helm + Terraform examples | Done | `deploy/helm/platform/`, `deploy/terraform/` |
| Docs aligned | Done | README, ADOPTER_GUIDE, cloud-deployment, blog |

## Security & secrets

| Check | Status | Notes |
|-------|--------|-------|
| No live API keys in tree | Pass | CI grep for `sk-…`, Fernet keys |
| `backend/.env` gitignored | Pass | `.gitignore` |
| `backend/platform.env` gitignored | Pass | `.gitignore` |
| `backend/hub.db` gitignored | Pass | `.gitignore` |
| Sample data uses `@example.com` | Pass | `mock_feedback.py`, directory fixtures |
| SECURITY.md contact | Done | `security@deriv.com` + GitHub advisories |

## Legal & compliance

| Check | Status | Notes |
|-------|--------|-------|
| Project license | MIT | `LICENSE` |
| Third-party summary | Done | `THIRD_PARTY_NOTICES.md` |
| No internal hostnames in code | Pass | CI + manual grep |
| No employee PII in fixtures | Pass | fictional `@example.com` only |
| Overnight internal prompts | Removed | CUT in P0 |
| Export / patent review | **Org action** | Deriv Legal to confirm Slack + sandbox docs for your jurisdictions before external announcement |

## Comms

| Item | Value |
|------|-------|
| Public repository | https://github.com/deriv-com/deriv-one-oss |
| Clone URL | `git clone https://github.com/deriv-com/deriv-one-oss.git` |
| Blog byline | Ansh Choudhary, Joel Joseph, Aditya Singhal, Sejal Chopra, Khirthana Sundaram |
| Announcement doc | `docs/blog-open-source-release.md` |

## Pre-push checklist (release engineer)

1. Public repo: `deriv-com/deriv-one-oss` — https://github.com/deriv-com/deriv-one-oss
2. `git init`, first commit — confirm `git status` shows **no** `.env`, `hub.db`, or `platform.env`.
3. Enable GitHub Security Advisories on the repo.
4. Push `main`, verify CI green.
5. Publish blog post after repo is live.
