# Build from repository root:
#   docker build -f backend/Dockerfile -t platform-backend .
FROM python:3.12-slim

RUN apt-get update \
    && apt-get install -y --no-install-recommends gosu curl ca-certificates git \
    && rm -rf /var/lib/apt/lists/*

# The container sandbox provider talks to a container engine over its socket,
# which needs a client binary as well as the socket itself. The static CLI is
# used rather than the distro `docker.io` package because that one drags in a
# daemon this image will never run. Drop this layer if you set
# SANDBOX_PROVIDER=subprocess or point CONTAINER_ENGINE at something else.
ARG DOCKER_CLI_VERSION=27.3.1
RUN set -eux; \
    arch="$(uname -m)"; \
    curl -fsSL "https://download.docker.com/linux/static/stable/${arch}/docker-${DOCKER_CLI_VERSION}.tgz" -o /tmp/docker.tgz; \
    tar -xzf /tmp/docker.tgz -C /tmp docker/docker; \
    install -m 0755 /tmp/docker/docker /usr/local/bin/docker; \
    rm -rf /tmp/docker.tgz /tmp/docker; \
    docker --version

RUN useradd --create-home --uid 1000 appuser

WORKDIR /app

COPY backend/requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy the application as a directory rather than enumerating modules. The old
# per-file list had to be edited for every new module and silently broke the
# build when one was removed; .dockerignore already excludes tests, caches and
# local state.
COPY backend/ /app/

COPY backend/docker-entrypoint.sh /docker-entrypoint.sh

# Skills data is copied to a seed location inside the image.
# The entrypoint initialises the /src/data volume from this seed on first run
# so that runtime-published skills (written to /src/data at runtime) survive
# container rebuilds.
COPY src/data/skills /app/skills-seed/skills
COPY src/data/skills-acl.yaml /app/skills-seed/skills-acl.yaml

RUN chmod +x /docker-entrypoint.sh \
    && mkdir -p /data /app/generated /src/data/skills /sandbox-workspaces \
    && chown -R appuser:appuser /data /app/generated /src/data /app/skills-seed /sandbox-workspaces

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    HUB_DATABASE_PATH=/data/hub.db

ARG BUILD_COMMIT=unknown
ENV BUILD_COMMIT=${BUILD_COMMIT}

EXPOSE 8000

ENTRYPOINT ["/docker-entrypoint.sh"]
CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8000"]
