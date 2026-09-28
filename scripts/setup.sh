#!/usr/bin/env bash
# Interactive terminal setup — writes backend/platform.env via setup_cli.py
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT/backend"

if [[ ! -d .venv ]]; then
  echo "No backend virtualenv found."
  echo "Create one first:"
  echo "  cd backend && python3 -m venv .venv && .venv/bin/pip install -r requirements.txt"
  exit 1
fi

exec .venv/bin/python setup_cli.py "$@"
