output "database_host" {
  description = "RDS hostname"
  value       = aws_db_instance.postgres.address
}

output "database_port" {
  description = "RDS port"
  value       = aws_db_instance.postgres.port
}

output "database_name" {
  description = "Database name"
  value       = var.db_name
}

output "database_username" {
  description = "Database username"
  value       = var.db_username
}

output "database_url_secret_arn" {
  description = "Secrets Manager ARN containing the asyncpg DATABASE_URL"
  value       = aws_secretsmanager_secret.database_url.arn
  sensitive   = true
}

output "s3_bucket_name" {
  description = "S3 bucket for blob storage (wire via BLOB_STORE plugin)"
  value       = aws_s3_bucket.blobs.bucket
}

output "helm_secret_hint" {
  description = "Example Helm --set for DATABASE_URL (fetch value from Secrets Manager in CI)"
  value       = "Fetch DATABASE_URL from secret ${aws_secretsmanager_secret.database_url.name} and pass to Helm secret.DATABASE_URL"
}
