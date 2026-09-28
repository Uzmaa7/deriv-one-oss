# Runbook: Slack token rotation

The platform stores Slack bot tokens and signing secrets in the **secret vault**
(`SECRET_BACKEND`), not in the database. Tokens expire according to Slack's
rotation policy — your operations team must refresh them before expiry.

This repository does **not** ship a token-rotation daemon. Implement rotation in
your private ops repo, a cron job, or your secret manager's rotation feature.

## OAuth v2 bot tokens (`xoxb-`)

Refresh via Slack's OAuth endpoint:

```http
POST https://slack.com/api/oauth.v2.access
Content-Type: application/x-www-form-urlencoded

client_id=...&client_secret=...&grant_type=refresh_token&refresh_token=...
```

On success, persist **both** the new `access_token` and `refresh_token` through
your `SECRET_BACKEND` implementation (same refs the agent row already uses).
Never log token values.

## App configuration tokens (`xoxe-`)

Platform-level Slack auto-create uses configuration tokens. Rotate via:

```http
POST https://slack.com/api/tooling.tokens.rotate
Authorization: Bearer <current-access-token>

{"refresh_token": "<refresh-token>"}
```

Update `SLACK_APP_CONFIGURATION_ACCESS_TOKEN` and
`SLACK_APP_CONFIGURATION_REFRESH_TOKEN` in your deployment secret store.

## Platform agent and user agents

- Each registered agent has `signing_secret_ref` and bot token refs on the `Agent`
  row — resolve and update through `secret_vault.py` / your backend API, not by
  editing the database directly.
- After rotation, restart is **not** always required: event handlers read signing
  secrets from the vault per request. Env-based platform tokens need a pod restart
  or config reload.

## Checklist

- [ ] Monitor Slack token expiry (Slack app settings → OAuth & Permissions)
- [ ] Rotate refresh token pairs atomically (access + refresh together)
- [ ] Verify `POST /api/slack/events` still validates signatures after signing-secret rotation
- [ ] Audit: log `app_id` / `agent_id` only — never token values

## Related

- `backend/slack_oauth.py` — OAuth flows and manifest sync
- [ADOPTER_GUIDE.md](../ADOPTER_GUIDE.md) — Slack setup
- [slack-dmz-relay.md](../slack-dmz-relay.md) — multi-replica events
