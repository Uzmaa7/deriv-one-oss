# Docker setup: logging in and running each component

This covers two things the main [README](../README.md) only summarises: how
authentication actually resolves to an admin or a member, and how to bring up
each part of the platform — core app and sandboxed execution — under Docker
specifically.

If you only need the quickest path to a running instance, use the
[Quick start](../README.md#quick-start) in the README. This document is for when
you need to understand *why* you're logged in as who you are, or you need a
component the base `docker compose up` doesn't start on its own.

---

## Contents

- [Logging in](#logging-in)
  - [Mock auth (default for `docker compose up`)](#mock-auth-default-for-docker-compose-up)
  - [Real identity provider](#real-identity-provider)
  - [Who counts as admin vs member](#who-counts-as-admin-vs-member)
- [Core stack](#core-stack)
- [Sandboxed code execution](#sandboxed-code-execution)
- [Knowledge base retrieval](#knowledge-base-retrieval)
- [Putting it together](#putting-it-together)

---

## Logging in

### Mock auth (default for `docker compose up`)

`backend/.env.example` ships with `ENABLE_MOCK_AUTH=true` so that the stack
works before you have an identity provider: every request is treated as one
fixed identity. It is the entire authentication check — with it on, anyone who
can reach the port is `MOCK_USER_EMAIL`, an admin. Turn it off before anyone
else can reach this.

```bash
# backend/.env
ENABLE_MOCK_AUTH=true
MOCK_USER_EMAIL=dev@localhost.local
MOCK_USER_SUB=mock-subject
MOCK_USER_GROUPS=platform-admins,platform-users
```

There is no login screen in this mode — open the UI and you are already signed
in as whatever `MOCK_USER_*` says. To see the platform **as an admin**, keep
`platform-admins` (or whatever your `ADMIN_GROUPS` names, below) in
`MOCK_USER_GROUPS`. To see it **as an ordinary member**, drop the admin group:

```bash
MOCK_USER_GROUPS=platform-users
```

Restart the backend after changing this — it's read once at process start
(`cf_access.py`, `MOCK_USER_GROUPS_TUPLE`). There is no way to switch identity
at runtime in this mode; edit the env var and restart.

**Never enable this anywhere reachable by anyone but you.** It skips signature
verification entirely.

### Real identity provider

Set `ENABLE_MOCK_AUTH=false` and point the backend at your OIDC provider:

```bash
OIDC_ISSUER=https://your-issuer.example.com
POLICY_AUD=<your application's audience value>
# Optional — auto-discovered when unset:
# OIDC_JWKS_URL=https://your-issuer.example.com/.well-known/jwks.json
```

The backend verifies a signed JWT and reads the caller's `sub` and email from
it. JWKS resolution order: explicit `OIDC_JWKS_URL` → OIDC discovery at
`{OIDC_ISSUER}/.well-known/openid-configuration` → Cloudflare Access certs
when the issuer looks like Cloudflare Access. See [ADOPTER_GUIDE.md](ADOPTER_GUIDE.md)
for Okta, Entra, Keycloak, and other recipes.

Either way, the token can arrive as `Cf-Access-Jwt-Assertion` (edge-injected) or
as a standard `Authorization: Bearer <token>` header — the second works for any
proxy or for calling the API directly.

### Who counts as admin vs member

Once a request is authenticated, role comes from group claims in the token —
nothing else:

```bash
ADMIN_GROUPS=platform-admins       # comma-separated, case-insensitive
MEMBER_GROUPS=platform-users
# READONLY_GROUPS=platform-observers
```

- A caller whose groups intersect `ADMIN_GROUPS` is an admin.
- Otherwise, if their groups intersect `MEMBER_GROUPS`, they're a member.
- If `REQUIRE_GROUP_MEMBERSHIP=false` (the default), **any** authenticated
  caller who matches neither list is still treated as a member — useful before
  you've wired up real groups. Set it `true` once you have.
- `ADMIN_EMAILS=you@example.org` grants admin by address, bypassing groups
  entirely. This exists to bootstrap the very first administrator on a fresh
  deployment with no groups yet — move to `ADMIN_GROUPS` once you can, and leave
  this empty in normal operation.

The frontend has no separate concept of "admin mode" — it calls `GET /api/me`,
reads `role` from the response, and shows or hides admin-only UI (the sidebar's
Blueprints, Computer, and Admin pages) based on that single value
(`src/feedback/FeedbackProvider.tsx`, `lib/SkillAccessContext.tsx`). There's
nothing to configure on the frontend side — get the role right on the backend
and the UI follows.

---

## Core stack

This is what `docker compose up --build` starts: the API and the web UI.

```bash
cp backend/.env.example backend/.env
```

Set at minimum:

```bash
# `localhost` here is the backend container, not your machine. For a gateway on
# your machine use host.docker.internal (Docker Desktop) or 172.17.0.1 (Linux).
MODEL_GATEWAY_URL=http://host.docker.internal:4000
MODEL_GATEWAY_API_KEY=...
SECRET_ENCRYPTION_KEY=$(python -c "import os,base64;print(base64.b64encode(os.urandom(32)).decode())")
```

```bash
docker compose up --build
```

Two services come up:

| Service | Built from | Exposes |
|---|---|---|
| `backend` | `backend/Dockerfile` | `8000` internally only (`expose:`, not `ports:`) |
| `frontend` | `Dockerfile` (root) | `${HTTP_PORT:-8080}` on the host |

`frontend` is nginx serving the built React app and reverse-proxying `/api/`,
`/apps/`, `/webhooks/` and `/internal/` to `http://backend:8000`
(`docker/nginx/default.conf` — the upstream name matches the Compose service
name `backend`, so this only works inside the Compose network as written). The
UI is at `http://localhost:8080` once `frontend` reports healthy, which it
waits for via `backend`'s healthcheck against `/api/health`.

State lives in three named volumes, all local to your machine:

```yaml
platform-data:        # SQLite DB, blobs, secrets, retrieval index (/data)
platform-generated:    # files a sandboxed run wrote (/app/generated)
platform-skills:       # the skill catalogue, mirrored from the blob store (/src/data)
```

Delete them (`docker compose down -v`) for a clean slate; nothing outside the
volumes and your `.env` file persists.

`docker-compose.yml` mounts `/var/run/docker.sock` into the backend container.
That's not incidental — it's how the sandbox provider, below, works
without a separate service. It also means the backend container can start and
stop arbitrary containers on your host. Remove the mount and set
`SANDBOX_PROVIDER` accordingly if you don't want that.

---

## Sandboxed code execution

Model-generated code needs somewhere to run. `SANDBOX_PROVIDER=container` (the
default) launches one disposable container per run using whatever
`CONTAINER_ENGINE` names — `docker` by default, matching the socket already
mounted into the backend container above.

A run has **no network**, so it cannot install anything: every package the
generated code imports has to already be in the image. `docker compose up`
therefore builds `docker/sandbox/Dockerfile`, tags it
`platform/sandbox-python:latest`, and sets two variables on the backend:

```yaml
SANDBOX_PYTHON_IMAGE: platform/sandbox-python:latest
SANDBOX_PYTHON_PACKAGES: requests, httpx, pandas, numpy, openpyxl, ...
```

Those two must agree. `SANDBOX_PYTHON_PACKAGES` is not what gets installed — it
is what the **model is told** it may import, and the model has no way to check.
Add a package to the image and leave the list alone and nothing will use it; do
the reverse and you get generated code that reliably fails on import. With
neither set, the fallback is bare `python:3.12-slim` and the model is told it
has the standard library only.

To use your own images instead:

```bash
docker build -t my-python-sandbox:1 docker/sandbox/

# docker/sandbox-node/Dockerfile.local is a local-buildable variant; the
# production docker/sandbox-node/Dockerfile targets an orchestrator-specific
# base image not meant for a plain Docker host.
docker build -f docker/sandbox-node/Dockerfile.local \
  -t node-sandbox:local docker/sandbox-node/
```

```bash
# backend/.env — note docker-compose.yml sets the Python ones itself, and
# values in that file win over backend/.env. Edit compose, or drop them there.
SANDBOX_NODE_IMAGE=node-sandbox:local
SANDBOX_PYTHON_IMAGE=my-python-sandbox:1
SANDBOX_PYTHON_PACKAGES=whatever that image has
```

### What the container boundary is worth

One throwaway container per run: network off, memory and CPU capped, read-only
root filesystem, all capabilities dropped, `no-new-privileges`, no credentials
mounted, a fresh workspace each time, and the run executes as the backend's own
uid rather than root. The kernel is **shared with the host**, so this stops the
ordinary failure modes but is not a defence against a kernel-level escape.

Nothing here installs a stronger runtime, and none ships in this repository. For
a real kernel boundary, install gVisor or Kata on the host, register it with
your engine, and set `SANDBOX_RUNTIME` (`runsc` or `kata`) — the provider passes
it through as `docker run --runtime <value>`. Whatever you end up with, the
provider states it in `SandboxProvider.isolation` and the UI shows that string,
so what operators are told tracks what they configured.

### Workspaces

The backend stages each run's code in a directory and bind-mounts it in. When
the backend is itself a container driving the *host's* engine — which is what
`docker compose` sets up — that directory has to be one the engine can also see,
because the engine resolves mount paths in its own filesystem. Compose mounts
the `sandbox-workspaces` volume at `/sandbox-workspaces` and sets
`SANDBOX_WORKSPACE_ROOT` to match; the host-side name is read back from the
engine, so it needs no configuration. If that lookup can't work for your setup,
set `SANDBOX_HOST_WORKSPACE_ROOT` explicitly. If the backend runs its own engine
so both paths already agree, set `SANDBOX_ALLOW_UNSHARED_WORKSPACE=1`.

Getting this wrong used to mean an empty `/workspace` and a run that could not
find its own code; it is now a startup error that says which variable to set.

**No container engine at all?** Set `SANDBOX_PROVIDER=subprocess` and
`ALLOW_UNSANDBOXED_EXECUTION=1`. This runs generated code as a plain child
process of the backend, with **no isolation whatsoever** — full access to
whatever the backend container (or host, outside Docker) can see. Development
only, on a machine only you can reach.

---

## Knowledge base retrieval

No separate setup — `RETRIEVAL_PROVIDER=local` (the default) is a full-text
index living in the same `platform-data` volume as everything else
(`RETRIEVAL_ROOT`, defaulting to `/data/retrieval` inside the container). Give
an agent a knowledge base from the Agent page and it works immediately.

---

## Putting it together

A full local stack with sandboxed execution, through one `docker compose`
invocation:

```bash
cp backend/.env.example backend/.env
# edit backend/.env:
#   MODEL_GATEWAY_URL, MODEL_GATEWAY_API_KEY, SECRET_ENCRYPTION_KEY  (required)
#   ENABLE_MOCK_AUTH=true, MOCK_USER_GROUPS=platform-admins,platform-users

docker compose up --build
```

Open `http://localhost:8080` — you're signed in as the mock admin, and
sandboxed code execution works out of the box.
