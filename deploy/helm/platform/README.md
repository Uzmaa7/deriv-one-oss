# Platform Helm chart

Minimal Helm chart for running the open-source platform on GKE, EKS, or any
Kubernetes cluster. It mirrors the reference manifests in `deploy/k8s/` with
configurable values and optional Slack workers.

For architecture, managed services, and production checklists, see
[docs/cloud-deployment.md](../../docs/cloud-deployment.md).

## Prerequisites

- Kubernetes 1.25+
- Helm 3.10+
- Container images built and pushed (see cloud-deployment doc)
- PostgreSQL `DATABASE_URL` for more than one API replica
- OpenAI-compatible model gateway (`MODEL_GATEWAY_URL` / `MODEL_GATEWAY_API_KEY`)

## Install

1. Build and push images:

   ```bash
   docker build -f backend/Dockerfile -t REGISTRY/platform-backend:TAG .
   docker build -f Dockerfile -t REGISTRY/platform-frontend:TAG .
   ```

2. Create a values override (recommended — do not commit secrets):

   ```yaml
   # my-values.yaml
   images:
     backend:
       repository: REGISTRY/platform-backend
       tag: TAG
     frontend:
       repository: REGISTRY/platform-frontend
       tag: TAG

   config:
     APP_BASE_URL: https://platform.example.com
     MODEL_GATEWAY_URL: https://your-gateway.example.com/v1

   secret:
     SECRET_ENCRYPTION_KEY: "<fernet-key>"
     MODEL_GATEWAY_API_KEY: "<gateway-key>"
     DATABASE_URL: "postgresql+asyncpg://user:pass@db-host:5432/platform"

   ingress:
     host: platform.example.com
   ```

3. Install:

   ```bash
   helm upgrade --install platform ./deploy/helm/platform \
     -f my-values.yaml \
     --namespace platform --create-namespace
   ```

## External Secrets

To manage secrets outside Helm:

```yaml
secret:
  create: false
  existingSecret: platform-secrets   # created by External Secrets Operator
```

## Optional Slack workers

Enable when `MESSAGE_QUEUE` points at a durable queue plugin:

```yaml
slackConsumer:
  enabled: true
  replicas: 1

# warmWorker is optional/advanced — do not run both consumers on the same queue
warmWorker:
  enabled: false
```

## Render without applying

```bash
helm template platform ./deploy/helm/platform -f my-values.yaml
```

## Related

- `deploy/k8s/` — plain YAML equivalent
- `deploy/terraform/` — optional AWS/GCP data-plane examples (RDS, S3, GCS)
- [docs/cloud-deployment.md](../../docs/cloud-deployment.md)
