#!/usr/bin/env bash
# Fail CI if secrets or org-specific hosts appear in tracked files.
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"

FAIL=0

check_absent() {
  local label="$1"
  local pattern="$2"
  if git rev-parse --is-inside-work-tree >/dev/null 2>&1; then
    if git grep -nE "$pattern" -- ':!scripts/check-forbidden-patterns.sh' ':!docs/' ':!*.example' ':!*.example.*' 2>/dev/null; then
      echo "ERROR: $label matched in tracked files"
      FAIL=1
    fi
  else
    if rg -n "$pattern" -g '!scripts/check-forbidden-patterns.sh' -g '!docs/**' -g '!**/*.example' -g '!**/*.example.*' . 2>/dev/null; then
      echo "ERROR: $label matched"
      FAIL=1
    fi
  fi
}

# Never commit live env files, local DB, or runtime secret stores
if git rev-parse --is-inside-work-tree >/dev/null 2>&1; then
  for sensitive in \
    backend/.env \
    backend/platform.env \
    backend/hub.db \
    backend/.slack_token_cache.json \
    deploy/k8s/secret.yaml \
    deploy/k8s/configmap.yaml; do
    if [[ -f "$sensitive" ]] && git ls-files --error-unmatch "$sensitive" >/dev/null 2>&1; then
      echo "ERROR: $sensitive is tracked — must stay gitignored"
      FAIL=1
    fi
  done
  if [[ -d backend/var ]] && git ls-files backend/var/ 2>/dev/null | grep -q .; then
    echo "ERROR: backend/var/ contains tracked files — local secrets/blobs must not ship"
    FAIL=1
  fi
fi

# Hostnames/URLs only — allow security@deriv.com in SECURITY.md / CODE_OF_CONDUCT.md
check_absent "Deriv gateway host" '([a-zA-Z0-9_*.-]+\.)deriv\.(ai|com|dev)|https?://[^[:space:]"'\'']*deriv\.(ai|com|dev)'
check_absent "LiteLLM host" 'litellm'
check_absent "OpenAI-style API key" 'sk-[A-Za-z0-9]{20,}'
check_absent "Likely Fernet key in env" 'SECRET_ENCRYPTION_KEY=[A-Za-z0-9+/=]{20,}'
check_absent "Vendor skill author" 'author:\s*deriv-one'
check_absent "Sample PIC email" 'jordan\.lee'
check_absent "Sample PIC team" 'team_compliance_ops'
check_absent "Legacy sandbox image tag" 'deriv-one-sandbox'
check_absent "Legacy backend image tag" 'deriv-one-backend'
check_absent "Legacy frontend image tag" 'deriv-one-frontend'

if [[ "$FAIL" -ne 0 ]]; then
  echo "Forbidden-pattern check failed."
  exit 1
fi

echo "Forbidden-pattern check passed."
