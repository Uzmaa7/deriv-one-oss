# Security policy

## Supported versions

Security fixes are applied on the default branch. Self-hosters should track `main`
or the latest release tag.

## Reporting a vulnerability

**Do not** open public GitHub issues for security-sensitive reports.

Use one of these channels:

1. **GitHub private security advisory** (preferred): [Report a vulnerability](https://github.com/deriv-com/deriv-one-oss/security/advisories/new) on `deriv-com/deriv-one-oss`.
2. **Email**: [security@deriv.com](mailto:security@deriv.com) — include the repository name and affected component.

Include:

- Description and impact
- Steps to reproduce
- Affected component (auth, sandbox, Slack relay, internal API, …)
- Suggested fix if you have one

We aim to acknowledge reports within a few business days.

## Deployment expectations for self-hosters

- **`ENABLE_MOCK_AUTH`** — development only. Must be off in production; the application refuses to start with mock auth when `APP_ENV=production`.
- **`/internal/*`** — not protected by end-user JWT. Isolate at the network layer and require `INTERNAL_SIGNING_SECRET` on callers.
- **Sandbox** — default container sandbox shares the host kernel unless you use gVisor/Kata. Review `SANDBOX_PROVIDER` and never mount the Docker socket in production unless you accept the risk.
- **Slack App Configuration tokens** — org-admin scope; store in a vault, rotate regularly.
- **Secrets** — never commit `backend/.env` or `backend/platform.env`. Use `RESOURCE_PREFIX` and `APP_ENV` to namespace shared backends.

## Secure defaults

The shipped `docker compose` stack uses mock auth and local providers for local
development only. Production deployments must configure real OIDC, turn off mock
auth, and follow [docs/ADOPTER_GUIDE.md](docs/ADOPTER_GUIDE.md).
