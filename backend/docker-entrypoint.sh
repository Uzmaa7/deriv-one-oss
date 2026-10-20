#!/usr/bin/env sh
set -eu
mkdir -p /data /app/generated /src/data/skills "${SANDBOX_WORKSPACE_ROOT:-/sandbox-workspaces}"

# Seed the skills volume from the image snapshot on first run.
# The check uses skills-acl.yaml as the sentinel: if the file is missing the
# volume is either new or empty, so we copy the full seed across.
if [ ! -f /src/data/skills-acl.yaml ]; then
    echo "Seeding skills data from image snapshot..."
    cp /app/skills-seed/skills-acl.yaml /src/data/skills-acl.yaml
    cp -r /app/skills-seed/skills/. /src/data/skills/
    echo "Skills seed complete."
fi

if [ "${RUN_MIGRATIONS:-false}" = "true" ]; then
    echo "Running Alembic migrations..."
    cd /app && alembic upgrade head
    echo "Migrations complete."
fi

if [ "$(id -u)" = "0" ]; then
    chown -R appuser:appuser /data /app/generated /src/data 2>/dev/null || true
    # Only the root itself: run workspaces inside it are created per run and
    # a recursive chown would fight with a run that is still writing.
    chown appuser:appuser "${SANDBOX_WORKSPACE_ROOT:-/sandbox-workspaces}" 2>/dev/null || true
    # The engine socket is root-owned on the host; let appuser reach it so the
    # container sandbox works without running this service as root.
    if [ -S /var/run/docker.sock ]; then
        chown root:appuser /var/run/docker.sock 2>/dev/null || true
        chmod 660 /var/run/docker.sock 2>/dev/null || true
    fi
    exec gosu appuser "$@"
fi
exec "$@"
