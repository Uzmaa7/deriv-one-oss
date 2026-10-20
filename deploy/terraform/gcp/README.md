# GCP Terraform example

Provisions a private-IP Cloud SQL PostgreSQL instance, a GCS bucket, and a
Secret Manager secret with the `DATABASE_URL` connection string.

**Does not create:** GKE, Workload Identity bindings, Memorystore, Pub/Sub, or
the Helm release.

## Prerequisites

- VPC with private service access configured for Cloud SQL
- `vpc_network` set to the VPC self-link (e.g. `projects/PROJECT/global/networks/NETWORK`)

## Usage

```bash
cd deploy/terraform/gcp

cat > terraform.tfvars <<'EOF'
project_id  = "my-gcp-project"
name_prefix = "acme-platform"
region      = "us-central1"
vpc_network = "projects/my-gcp-project/global/networks/default"
EOF

terraform init
terraform plan
terraform apply
```

## Wire into Helm

```bash
DATABASE_URL=$(gcloud secrets versions access latest \
  --secret="$(terraform output -raw database_url_secret_id)")

helm upgrade --install platform ../../helm/platform \
  --namespace platform --create-namespace \
  --set secret.DATABASE_URL="$DATABASE_URL" \
  --set config.APP_BASE_URL=https://platform.example.com
```

Use `terraform output -raw gcs_bucket_name` when implementing your `BLOB_STORE`
GCS plugin.

See [docs/cloud-deployment.md](../../../docs/cloud-deployment.md) for the full
production checklist.
