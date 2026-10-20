# AWS Terraform example

Provisions a private RDS PostgreSQL instance, an encrypted S3 bucket, and a
Secrets Manager secret with the `DATABASE_URL` connection string.

**Does not create:** EKS, IRSA policies, ElastiCache, SQS, or the Helm release.

## Usage

```bash
cd deploy/terraform/aws

cat > terraform.tfvars <<'EOF'
name_prefix            = "acme-platform"
vpc_id                 = "vpc-xxxxxxxx"
db_subnet_ids          = ["subnet-aaa", "subnet-bbb"]
db_allowed_cidr_blocks = ["10.0.0.0/16"]
aws_region             = "us-east-1"
EOF

terraform init
terraform plan
terraform apply
```

## Wire into Helm

```bash
# Read DATABASE_URL from Secrets Manager (example)
DATABASE_URL=$(aws secretsmanager get-secret-value \
  --secret-id "$(terraform output -raw database_url_secret_arn)" \
  --query SecretString --output text)

helm upgrade --install platform ../../helm/platform \
  --namespace platform --create-namespace \
  --set secret.DATABASE_URL="$DATABASE_URL" \
  --set config.APP_BASE_URL=https://platform.example.com
```

Point `BLOB_STORE` at your private S3 provider plugin using
`terraform output -raw s3_bucket_name`.

See [docs/cloud-deployment.md](../../../docs/cloud-deployment.md) for the full
production checklist.
