locals {
  name         = var.name_prefix
  instance_name = "${local.name}-postgres"
  bucket_name   = "${local.name}-platform-blobs"
}

resource "random_password" "db" {
  length  = 32
  special = false
}

resource "google_sql_database_instance" "postgres" {
  name             = local.instance_name
  database_version = "POSTGRES_16"
  region           = var.region

  settings {
    tier = var.db_tier

    ip_configuration {
      ipv4_enabled    = false
      private_network = var.vpc_network
    }

    backup_configuration {
      enabled = true
    }
  }

  deletion_protection = false
}

resource "google_sql_database" "platform" {
  name     = var.db_name
  instance = google_sql_database_instance.postgres.name
}

resource "google_sql_user" "platform" {
  name     = var.db_username
  instance = google_sql_database_instance.postgres.name
  password = random_password.db.result
}

resource "google_storage_bucket" "blobs" {
  name                        = local.bucket_name
  location                    = var.region
  uniform_bucket_level_access = true
  force_destroy               = var.gcs_force_destroy

  versioning {
    enabled = true
  }
}

resource "google_secret_manager_secret" "database_url" {
  secret_id = "${local.name}-platform-database-url"

  replication {
    auto {}
  }
}

resource "google_secret_manager_secret_version" "database_url" {
  secret      = google_secret_manager_secret.database_url.id
  secret_data = format(
    "postgresql+asyncpg://%s:%s@%s/%s",
    var.db_username,
    random_password.db.result,
    google_sql_database_instance.postgres.private_ip_address,
    var.db_name,
  )
}
