output "bucket_name" {
  description = "Private bucket with encrypted TorKit backups."
  value       = aws_s3_bucket.recovery.bucket
}

output "restic_repository" {
  description = "Set RESTIC_REPOSITORY on the TorKit VPS."
  value       = "s3:s3.${var.aws_region}.amazonaws.com/${aws_s3_bucket.recovery.bucket}/torkit"
}

output "backup_iam_user" {
  description = "Create a scoped key separately; never store it in Terraform state."
  value       = aws_iam_user.restic.name
}
