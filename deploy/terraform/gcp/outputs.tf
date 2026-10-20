output "database_private_ip" {
  description = "Cloud SQL private IP"
  value       = google_sql_database_instance.postgres.private_ip_address
}

output "database_name" {
  description = "Database name"
  value       = var.db_name
}

output "database_username" {
  description = "Database username"
  value       = var.db_username
}

output "database_url_secret_id" {
  description = "Secret Manager secret ID containing DATABASE_URL"
  value       = google_secret_manager_secret.database_url.secret_id
}

output "gcs_bucket_name" {
  description = "GCS bucket for blob storage (wire via BLOB_STORE plugin)"
  value       = google_storage_bucket.blobs.name
}

output "helm_secret_hint" {
  description = "Example Helm wiring for DATABASE_URL"
  value       = "Fetch DATABASE_URL from Secret Manager ${google_secret_manager_secret.database_url.secret_id} and pass to Helm secret.DATABASE_URL"
}
