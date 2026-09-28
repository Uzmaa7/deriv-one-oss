# Terraform examples (optional)

Minimal, **starter** Terraform for the platform data plane. These examples
provision managed Postgres and object storage — **not** a Kubernetes cluster,
ingress controller, or identity proxy.

Use them to bootstrap shared infrastructure, then wire outputs into the
[Helm chart](../helm/platform/) or `deploy/k8s/` manifests.

| Example | Provisions |
|---------|------------|
| [aws/](aws/) | RDS PostgreSQL, S3 bucket, Secrets Manager secret with connection string |
| [gcp/](gcp/) | Cloud SQL PostgreSQL, GCS bucket, Secret Manager secret with connection string |

## Typical flow

1. Apply Terraform in your cloud account (bring your own VPC / private networking).
2. Build and push container images.
3. Install the Helm chart with `secret.DATABASE_URL` from Terraform outputs.
4. Implement `BLOB_STORE`, `SECRET_BACKEND`, and `MESSAGE_QUEUE` provider plugins
   for production multi-replica deployments — see
   [backend/providers_reference/README.md](../../backend/providers_reference/README.md).

## What is intentionally omitted

- EKS / GKE cluster creation
- IAM roles for service accounts (IRSA / Workload Identity) — add in your org repo
- Redis, Pub/Sub, SQS, VPC endpoints
- Cloud-specific provider Python packages (implement privately)

Adapt naming, encryption, backup, and network rules to your security standards
before production use.
