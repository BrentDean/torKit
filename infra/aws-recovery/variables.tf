variable "aws_region" {
  type        = string
  description = "AWS region for the recovery bucket."
  default     = "us-east-1"
}

variable "name_prefix" {
  type        = string
  description = "Unique lowercase prefix for bucket and IAM user."
  default     = "torkit-recovery"
  validation {
    condition     = can(regex("^[a-z][a-z0-9-]{2,34}$", var.name_prefix))
    error_message = "name_prefix must be 3-35 lowercase letters, digits or hyphens."
  }
}

variable "force_destroy" {
  type        = bool
  description = "Permit deleting all backup objects. Only enable for disposable labs."
  default     = false
}
