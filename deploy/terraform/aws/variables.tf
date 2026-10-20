variable "aws_region" {
  description = "AWS region"
  type        = string
  default     = "us-east-1"
}

variable "name_prefix" {
  description = "Prefix for resource names (lowercase, no spaces)"
  type        = string
}

variable "vpc_id" {
  description = "VPC where RDS and security groups are created"
  type        = string
}

variable "db_subnet_ids" {
  description = "Private subnet IDs for the RDS subnet group"
  type        = list(string)
}

variable "db_allowed_cidr_blocks" {
  description = "CIDR blocks allowed to reach PostgreSQL (typically your EKS node / pod CIDR)"
  type        = list(string)
}

variable "db_instance_class" {
  description = "RDS instance class"
  type        = string
  default     = "db.t4g.micro"
}

variable "db_name" {
  description = "Initial database name"
  type        = string
  default     = "platform"
}

variable "db_username" {
  description = "Master database username"
  type        = string
  default     = "platform"
}

variable "db_allocated_storage_gb" {
  description = "Allocated storage for RDS (GiB)"
  type        = number
  default     = 20
}

variable "s3_force_destroy" {
  description = "Allow Terraform to delete the S3 bucket even when it contains objects (non-prod only)"
  type        = bool
  default     = false
}
