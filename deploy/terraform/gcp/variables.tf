variable "project_id" {
  description = "GCP project ID"
  type        = string
}

variable "region" {
  description = "GCP region"
  type        = string
  default     = "us-central1"
}

variable "name_prefix" {
  description = "Prefix for resource names (lowercase, no spaces)"
  type        = string
}

variable "vpc_network" {
  description = "VPC network self-link or name for private Cloud SQL IP"
  type        = string
}

variable "db_tier" {
  description = "Cloud SQL machine tier"
  type        = string
  default     = "db-f1-micro"
}

variable "db_name" {
  description = "Initial database name"
  type        = string
  default     = "platform"
}

variable "db_username" {
  description = "Database username"
  type        = string
  default     = "platform"
}

variable "gcs_force_destroy" {
  description = "Allow Terraform to delete the GCS bucket with objects (non-prod only)"
  type        = bool
  default     = false
}
