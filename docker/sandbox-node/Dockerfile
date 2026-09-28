# Node build sandbox for Lab — Overnight mode.
#
# Extends the standard python-runtime sandbox (keeps the port-8888 `python3 -c`
# runner that executor.py expects) and adds Node/npm plus a pre-baked Vite + React
# node_modules so overnight `vite build` runs do not pay a cold npm install.
FROM registry.k8s.io/agent-sandbox/python-runtime-sandbox:v0.1.0

USER root

# Node.js LTS via NodeSource
RUN apt-get update \
    && apt-get install -y --no-install-recommends curl ca-certificates \
    && curl -fsSL https://deb.nodesource.com/setup_20.x | bash - \
    && apt-get install -y --no-install-recommends nodejs \
    && rm -rf /var/lib/apt/lists/*

# Pre-bake a Vite + React toolchain so builds skip a cold install.
# The orchestrator (node_build.py) copies /opt/vite-template/node_modules into the
# project when the project has no node_modules of its own.
RUN mkdir -p /opt/vite-template
WORKDIR /opt/vite-template
RUN cat > package.json <<'EOF'
{
  "name": "vite-template",
  "private": true,
  "type": "module",
  "scripts": { "build": "vite build" },
  "dependencies": {
    "react": "^18.3.1",
    "react-dom": "^18.3.1"
  },
  "devDependencies": {
    "@vitejs/plugin-react": "^4.3.4",
    "vite": "^5.4.10",
    "tailwindcss": "^3.4.4",
    "autoprefixer": "^10.4.19",
    "postcss": "^8.4.38",
    "typescript": "^5.5.3",
    "@types/react": "^18.3.3",
    "@types/react-dom": "^18.3.0"
  }
}
EOF
# --ignore-scripts hardens against supply-chain postinstall execution.
RUN npm install --ignore-scripts --no-audit --no-fund \
    && chmod -R a+rX /opt/vite-template

# Make the pre-baked toolchain resolvable on PATH for `npx --no-install vite`.
ENV PATH="/opt/vite-template/node_modules/.bin:${PATH}"

WORKDIR /
COPY sandbox_server.py /sandbox_server.py
USER 65532
CMD ["uvicorn", "sandbox_server:app", "--host", "0.0.0.0", "--port", "8888"]
