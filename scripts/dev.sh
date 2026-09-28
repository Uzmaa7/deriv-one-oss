#!/usr/bin/env bash
# Start backend (uvicorn) and frontend (vite) together for local development.
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
BACKEND_PORT="${DEV_API_PORT:-8001}"
FRONTEND_PORT="${FRONTEND_PORT:-3000}"

if [[ ! -d "$ROOT/backend/.venv" ]]; then
  echo "No backend virtualenv. Run: npm run setup"
  exit 1
fi

cleanup() {
  local exit_code=$?
  if [[ -n "${BACKEND_PID:-}" ]] && kill -0 "$BACKEND_PID" 2>/dev/null; then
    kill "$BACKEND_PID" 2>/dev/null || true
    wait "$BACKEND_PID" 2>/dev/null || true
  fi
  if [[ -n "${FRONTEND_PID:-}" ]] && kill -0 "$FRONTEND_PID" 2>/dev/null; then
    kill "$FRONTEND_PID" 2>/dev/null || true
    wait "$FRONTEND_PID" 2>/dev/null || true
  fi
  exit "$exit_code"
}
trap cleanup EXIT INT TERM

backend_healthy() {
  curl -sf "http://127.0.0.1:${BACKEND_PORT}/api/health" >/dev/null 2>&1
}

BACKEND_PID=""
if backend_healthy; then
  echo "● Backend already running at http://127.0.0.1:${BACKEND_PORT}"
else
  echo "● Starting backend on http://127.0.0.1:${BACKEND_PORT} …"
  (
    cd "$ROOT/backend"
    exec .venv/bin/python -m uvicorn main:app --reload --host 127.0.0.1 --port "$BACKEND_PORT"
  ) &
  BACKEND_PID=$!

  for _ in $(seq 1 45); do
    if backend_healthy; then
      echo "● Backend ready"
      break
    fi
    if [[ -n "$BACKEND_PID" ]] && ! kill -0 "$BACKEND_PID" 2>/dev/null; then
      echo "Backend failed to start. Check logs above."
      exit 1
    fi
    sleep 1
  done
  if ! backend_healthy; then
    echo "Backend did not become healthy within 45s."
    exit 1
  fi
fi

echo "● Starting frontend on http://localhost:${FRONTEND_PORT} …"
(
  cd "$ROOT"
  export DEV_API_PORT="$BACKEND_PORT"
  exec npm run dev
) &
FRONTEND_PID=$!

echo ""
echo "════════════════════════════════════════════════════════════"
echo "  App:     http://localhost:${FRONTEND_PORT}"
echo "  API:     http://127.0.0.1:${BACKEND_PORT}"
echo "  Stop:    Ctrl+C"
echo "════════════════════════════════════════════════════════════"
echo ""

wait "$FRONTEND_PID"
