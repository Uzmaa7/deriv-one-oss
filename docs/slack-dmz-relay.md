# Slack DMZ relay — minimal implementation spec

This document describes a **minimal Slack events relay** for multi-replica
deployments. The relay is **not shipped** in this repository; adopters build and
operate it (Cloud Run, Lambda, a small K8s Deployment, etc.).

For the default single-instance path (no relay), see
[README.md § Running more than one instance](../README.md#running-more-than-one-instance).

---

## When you need this

| Topology | Slack Request URL | Relay |
|----------|-------------------|-------|
| 1 API replica (local, compose, small K8s) | `{APP_BASE_URL}/api/slack/events` | **Not needed** |
| 2+ API replicas + durable queue | `{SLACK_EVENTS_GATEWAY_URL}/slack/agent/{app_id}` | **Required** |

The platform backend sets manifest `request_url` from `SLACK_EVENTS_GATEWAY_URL`
when that env var is set (`backend/slack_oauth.py`).

---

## Architecture

```text
  Internet (Slack)
        │
        ▼
┌───────────────────┐
│  DMZ relay        │  public ingress, no DB, no agent secrets at rest
│  (your service)   │
└─────────┬─────────┘
          │ ① GET /internal/secrets/{app_id}  (private VPC / cluster network)
          ▼
┌───────────────────┐
│  Platform backend │  returns signing_secret for registered agents
└─────────┬─────────┘
          │
          │ ② publish envelope
          ▼
┌───────────────────┐
│  MESSAGE_QUEUE    │  topic: slack.events  (Pub/Sub, SQS, …)
└─────────┬─────────┘
          │
          ▼
┌───────────────────┐
│ slack_consumer_   │  or warm_worker — same backend image
│ worker            │
└───────────────────┘
```

Workers (`python -m slack_consumer_worker` or `python -m warm_worker`) consume
the queue and dispatch to agents. The API replicas do **not** receive Slack
POSTs directly in this topology.

---

## Relay HTTP API

### Route

```
POST /slack/agent/{app_id}
```

- `{app_id}` must match Slack's `api_app_id` (format `A` + alphanumeric, e.g.
  `A0123456789`).
- Slack's Event Subscriptions **Request URL** is
  `{SLACK_EVENTS_GATEWAY_URL}/slack/agent/{app_id}` (one URL per Slack app).

Optional health route (your choice):

```
GET /healthz  → 200
```

### Request headers (from Slack)

Forward these unchanged when verifying:

| Header | Purpose |
|--------|---------|
| `X-Slack-Signature` | HMAC-SHA256 signature (`v0=…`) |
| `X-Slack-Request-Timestamp` | Unix seconds; reject if \|now − ts\| > 300 |
| `X-Slack-Retry-Num` | Present on retries; log for observability |
| `Content-Type` | `application/json` |

Read the **raw body bytes** before JSON parsing — the signature is computed over
the exact body Slack sent.

### Response rules (Slack-facing)

Slack requires a response within **~3 seconds**. The relay must **never** wait for
LLM work — only verify, enqueue, and ACK.

| Case | HTTP | Body |
|------|------|------|
| `url_verification` | 200 | `{"challenge": "<challenge from payload>"}` |
| Valid `event_callback`, enqueued | 200 | `{"ok": true}` |
| Malformed JSON | 400 | `{"error": "invalid_json"}` |
| Missing `api_app_id` in body (and not in path) | 400 | `{"error": "missing_api_app_id"}` |
| Invalid / expired signature | 403 | empty or `{"error": "invalid_signature"}` |
| Unknown `app_id` (not registered) | 200 | `{"ok": true}` — stops Slack retries (matches backend behaviour) |
| Enqueue failure (transient) | 503 | Slack will retry |

**`url_verification`:** respond immediately with the challenge. The backend skips
signature verification for this type; the relay may do the same for speed during
manifest setup. Once the agent row exists, you may optionally verify the
signature before echoing the challenge.

---

## Signature verification

Same algorithm as `api_slack_events._verify_slack_signature`:

```python
import hashlib
import hmac
import time

TOLERANCE_SECS = 300

def verify_slack_signature(
    signing_secret: str,
    timestamp: str,
    raw_body: bytes,
    provided_sig: str,
) -> bool:
    try:
        ts = int(timestamp)
    except (ValueError, TypeError):
        return False
    if abs(time.time() - ts) > TOLERANCE_SECS:
        return False
    base = f"v0:{timestamp}:{raw_body.decode('utf-8', errors='replace')}"
    expected = "v0=" + hmac.new(
        signing_secret.encode(),
        base.encode(),
        hashlib.sha256,
    ).hexdigest()
    return hmac.compare_digest(expected, provided_sig)
```

Use **constant-time** comparison (`hmac.compare_digest`).

---

## Fetching signing secrets (private backend)

The relay must **not** store signing secrets in its own database. Fetch from the
platform on demand (with caching — see below).

### Request

```http
GET {PLATFORM_INTERNAL_BASE_URL}/internal/secrets/{app_id}
X-Invoke-Context: <service-jwt>
```

- `PLATFORM_INTERNAL_BASE_URL` — cluster-internal URL, e.g.
  `http://backend:8000` (K8s Service name must be `backend` for the bundled
  frontend nginx image, or your own Service DNS name).
- `/internal/*` is **blocked** on the public UI ingress (403). Call the backend
  Service directly on the private network.

### Service JWT

Sign with `INTERNAL_SIGNING_SECRET` (same value as the backend):

```python
# In your relay process — equivalent to backend api_internal.sign_service_token
import jwt, time

def sign_service_token(secret: str, service_id: str = "slack-relay-dmz", ttl: int = 86400) -> str:
    now = int(time.time())
    return jwt.encode(
        {"service": service_id, "iat": now, "exp": now + ttl},
        secret,
        algorithm="HS256",
    )
```

Refresh the token every **~23 hours** (default TTL 24 h).

Or call from a trusted bootstrap job on the backend:

```python
from api_internal import sign_service_token
token = sign_service_token("slack-relay-dmz")
```

### Response

| Status | Meaning |
|--------|---------|
| 200 | `{"signing_secret": "..."}` |
| 403 | Missing/invalid `X-Invoke-Context` |
| 404 | App not registered or secret not stored yet |
| 503 | Vault/backend error |

**Caching:** cache `(app_id → signing_secret)` in memory with a short TTL (e.g.
5–15 minutes) to reduce load. Invalidate on 403 signature failures (secret may
have rotated).

**Registration window:** during guided Slack registration, manifest push can
happen before the `Agent` row exists. `url_verification` may arrive while
`/internal/secrets/{app_id}` still returns 404 — echo the challenge anyway for
`url_verification` only.

---

## Queue envelope

After verification, publish to topic **`slack.events`** (override with
`SLACK_EVENTS_TOPIC` on workers).

### Required fields

```json
{
  "envelope_id": "550e8400-e29b-41d4-a716-446655440000",
  "slack_event_id": "Ev0ABC123",
  "api_app_id": "A0123456789",
  "team_id": "T0123456789",
  "type": "event_callback",
  "event": { "type": "app_mention", "user": "U…", "text": "…", "channel": "C…" },
  "received_at_ms": 1715610601234
}
```

| Field | Source |
|-------|--------|
| `envelope_id` | New UUID per publish (idempotency aid for your queue) |
| `slack_event_id` | `payload.event_id` from Slack |
| `api_app_id` | Path `{app_id}` or `payload.api_app_id` |
| `team_id` | `payload.team_id` |
| `type` | `payload.type` (`event_callback`) |
| `event` | `payload.event` (inner event object) |
| `received_at_ms` | `Date.now()` or equivalent |

Workers also accept top-level `event_id` as an alias for `slack_event_id`
(`slack_consumer_worker._process_message`).

### Publish semantics

- **At-least-once** delivery — workers deduplicate on `slack_event_id` in the DB.
- Return HTTP 200 to Slack **only after** the queue accepts the message.
- If publish fails, return **503** so Slack retries.
- Do **not** publish `url_verification` messages to the queue.

---

## Platform configuration

Set on the **backend** (and workers):

```bash
SLACK_EVENTS_GATEWAY_URL=https://slack-gateway.example.com
INTERNAL_SIGNING_SECRET=<shared-hs256-secret>
PLATFORM_INTERNAL_BASE_URL=http://backend:8000
MESSAGE_QUEUE=your.module:PubSubQueue   # durable — not inprocess
SLACK_EVENTS_TOPIC=slack.events         # default
```

Set on **frontend build** (optional, for UI copy-paste URLs):

```bash
VITE_SLACK_GATEWAY_BASE=https://slack-gateway.example.com
```

Run workers:

```bash
python -m slack_consumer_worker   # subprocess per event (max isolation)
# or
python -m warm_worker             # coroutine per event (higher throughput)
```

See `docker-compose.workers.yml` for compose equivalents.

---

## Minimal relay pseudocode

```python
@app.post("/slack/agent/{app_id}")
async def slack_agent(app_id: str, request: Request):
    raw = await request.body()
    payload = json.loads(raw)

    if payload.get("type") == "url_verification":
        return {"challenge": payload.get("challenge", "")}

    secret = await secrets_cache.get(app_id)  # GET /internal/secrets/{app_id}
    if not secret:
        return {"ok": True}  # unregistered app — stop Slack retries

    ts = request.headers.get("X-Slack-Request-Timestamp", "")
    sig = request.headers.get("X-Slack-Signature", "")
    if not verify_slack_signature(secret, ts, raw, sig):
        raise HTTPException(403)

    envelope = {
        "envelope_id": str(uuid.uuid4()),
        "slack_event_id": payload.get("event_id", ""),
        "api_app_id": app_id,
        "team_id": payload.get("team_id", ""),
        "type": payload.get("type", ""),
        "event": payload.get("event", {}),
        "received_at_ms": int(time.time() * 1000),
    }
    await queue.publish("slack.events", envelope)
    return {"ok": True}
```

---

## Cloud Run sketch

| Setting | Value |
|---------|-------|
| Ingress | **All** (public — Slack must reach it) |
| Min instances | 1+ (cold start can miss Slack's 3s deadline) |
| CPU | Always allocated during request |
| VPC | **Serverless VPC connector** (or Direct VPC) to reach `PLATFORM_INTERNAL_BASE_URL` |
| Secrets | `INTERNAL_SIGNING_SECRET`, queue credentials — **not** Slack signing secrets |
| IAM | Invoke Pub/Sub publish or SQS send only |

Do **not** mount the platform database or secret vault into the relay.

---

## Security checklist

- [ ] Relay has no copy of agent bot tokens or signing secrets at rest (fetch on demand + short cache only)
- [ ] `/internal/secrets` reachable only on private network
- [ ] `INTERNAL_SIGNING_SECRET` rotated with backend in lockstep
- [ ] Replay window enforced (5-minute timestamp tolerance)
- [ ] Rate limit public ingress (Slack IP ranges optional; signature is the real gate)
- [ ] Log `app_id`, `event_id`, `retry_num` — never log signing secrets or raw tokens

---

## Related docs

- [cloud-deployment.md](cloud-deployment.md) — Slack at scale, worker layout
- [ADOPTER_GUIDE.md](ADOPTER_GUIDE.md) — internal routes and multi-replica Slack
- `backend/api_internal.py` — `/internal/secrets` contract
- `backend/slack_consumer_worker.py` — consumer envelope schema
