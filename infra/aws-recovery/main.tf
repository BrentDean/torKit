terraform {
  required_version = ">= 1.6.0"
  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = ">= 5.0, < 7.0"
    }
  }
}

provider "aws" {
  region = var.aws_region
}

resource "aws_s3_bucket" "recovery" {
  bucket_prefix = "${var.name_prefix}-"
  force_destroy = var.force_destroy
  tags = {
    Project = "TorKit"
    Purpose = "EncryptedResticRecovery"
  }
}

resource "aws_s3_bucket_public_access_block" "recovery" {
  bucket                  = aws_s3_bucket.recovery.id
  block_public_acls       = true
  ignore_public_acls      = true
  block_public_policy     = true
  restrict_public_buckets = true
}

resource "aws_s3_bucket_ownership_controls" "recovery" {
  bucket = aws_s3_bucket.recovery.id
  rule {
    object_ownership = "BucketOwnerEnforced"
  }
}

resource "aws_s3_bucket_server_side_encryption_configuration" "recovery" {
  bucket = aws_s3_bucket.recovery.id
  rule {
    apply_server_side_encryption_by_default {
      sse_algorithm = "AES256"
    }
    bucket_key_enabled = false
  }
}

resource "aws_s3_bucket_lifecycle_configuration" "recovery" {
  bucket = aws_s3_bucket.recovery.id
  rule {
    id     = "abort-incomplete-multipart-uploads"
    status = "Enabled"
    filter {}
    abort_incomplete_multipart_upload {
      days_after_initiation = 7
    }
  }
}

data "aws_iam_policy_document" "deny_insecure_transport" {
  statement {
    sid     = "DenyInsecureTransport"
    effect  = "Deny"
    actions = ["s3:*"]
    resources = [
      aws_s3_bucket.recovery.arn,
      "${aws_s3_bucket.recovery.arn}/*",
    ]
    principals {
      type        = "*"
      identifiers = ["*"]
    }
    condition {
      test     = "Bool"
      variable = "aws:SecureTransport"
      values   = ["false"]
    }
  }
}

resource "aws_s3_bucket_policy" "recovery" {
  bucket     = aws_s3_bucket.recovery.id
  policy     = data.aws_iam_policy_document.deny_insecure_transport.json
  depends_on = [aws_s3_bucket_public_access_block.recovery]
}

resource "aws_iam_user" "restic" {
  name = "${var.name_prefix}-backup"
  tags = {
    Project = "TorKit"
    Purpose = "EncryptedResticRecovery"
  }
}

data "aws_iam_policy_document" "restic" {
  statement {
    sid       = "BucketMetadata"
    effect    = "Allow"
    actions   = ["s3:ListBucket", "s3:GetBucketLocation", "s3:ListBucketMultipartUploads"]
    resources = [aws_s3_bucket.recovery.arn]
  }
  statement {
    sid    = "TorKitRecoveryObjects"
    effect = "Allow"
    actions = [
      "s3:GetObject",
      "s3:PutObject",
      "s3:DeleteObject",
      "s3:AbortMultipartUpload",
      "s3:ListMultipartUploadParts",
    ]
    resources = ["${aws_s3_bucket.recovery.arn}/torkit/*"]
  }
}

resource "aws_iam_user_policy" "restic" {
  name   = "${var.name_prefix}-backup-bucket-only"
  user   = aws_iam_user.restic.name
  policy = data.aws_iam_policy_document.restic.json
}
