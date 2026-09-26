# TorKit AWS S3 recovery lab

This optional Terraform stack creates one private S3 Standard bucket and a
bucket-scoped IAM user/policy for Restic. Restic encrypts all backup data
before uploading it. S3 also applies AES256 server-side encryption. No AWS
access key or Restic password is created or stored in Terraform state.

## Provision

Run on the workstation with permissions to create AWS S3 and IAM resources:

    cd infra/aws-recovery
    terraform init
    terraform fmt -check
    terraform validate
    terraform plan -out=tfplan
    terraform apply tfplan
    terraform output restic_repository
    terraform output backup_iam_user

Change name_prefix if the default IAM user name already exists. Record the
repository output; it includes the region and actual unique bucket name.
Do not commit .tfstate, tfplan, passwords or AWS credentials.

Create an access key for the output IAM user separately through AWS IAM.
Copy it to ~/.config/torkit/backup.env on the VPS, not Terraform state or Git.
Use your password manager to preserve the Restic encryption password offline.

    RESTIC_REPOSITORY=s3:s3.us-east-1.amazonaws.com/OUTPUT-BUCKET/torkit
    RESTIC_PASSWORD_FILE=/home/torkit/.config/torkit/restic-password
    AWS_ACCESS_KEY_ID=YOUR_BUCKET_ONLY_USER_KEY
    AWS_SECRET_ACCESS_KEY=YOUR_BUCKET_ONLY_USER_SECRET

Replace RESTIC_REPOSITORY with the exact terraform output. Create a strong
Restic password if not already present:

    install -d -m 700 ~/.config/torkit
    umask 077
    python3 -c 'import secrets; print(secrets.token_urlsafe(48))' > ~/.config/torkit/restic-password
    chmod 600 ~/.config/torkit/restic-password ~/.config/torkit/backup.env

Load the environment and initialize only the NEW repository:

    set -a
    . ~/.config/torkit/backup.env
    set +a
    restic --repo "$RESTIC_REPOSITORY" --password-file "$RESTIC_PASSWORD_FILE" init
    cd /opt/torkit
    ./torkit backup --no-content
    ./torkit snapshots
    ./torkit backup-check

With --no-content, Board posts/uploads, all onion identities and config are
still backed up, but configured Share/Receive/Website directories are NOT.
Use regular ./torkit backup to include those directories.

## Destroy

With default force_destroy=false, terraform destroy refuses to empty a
bucket that contains backups. This is intentional.

A disposable lab may explicitly set force_destroy=true when provisioning
the bucket, then terraform destroy after confirming it no longer needs any
snapshots. Deleting the bucket deletes the backup copy of the identities and
Board history. Reapplying Terraform later creates a NEW EMPTY repository;
nothing is recovered from the old one.

If the access key was created outside Terraform, revoke and delete that key
first; otherwise AWS may reject deletion of its IAM user. Terraform does not
create standing compute instances. S3 Standard charges for stored bytes and
requests with no fixed 1-TB allocation; retrieval/transfer may also be billed.
