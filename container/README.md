# TorKit Docker services

TorKit keeps Docker Compose behind a small launcher:

```bash
./torkit          # same as ./torkit up
./torkit up       # choose stopped services to start
./torkit chat     # start/resume ephemeral conversation, preserving existing address
./torkit board    # start/resume persistent message board
./torkit messageboard  # alias for board
./torkit down     # choose running services to stop; preserve identities
./torkit restart  # choose running services to restart; preserve identities
./torkit reset    # choose services to receive new addresses and keys
./torkit burn board  # destroy Board posts and its onion identity
./torkit operator   # operator-only red Burn / Nuke controls on localhost
./torkit nuke     # destroy TorKit containers, image, and runtime identities
./torkit ps       # Docker container diagnostics
./torkit status   # onion addresses and private access keys
./torkit ensure board         # bring persistent Board online
./torkit ensure all           # reconcile every service
./torkit health --json        # machine-readable health without credentials
```

Every service-selection screen supports:

```text
a  all applicable services
q  exit without changes
```

`down` opens the same service-selection screen as the other lifecycle
commands. It stops only the selected running services and preserves their
onion addresses and authorization keys.

## Configuration

The launcher creates one protected configuration file:

```text
~/.torkit/config.env
```

```dotenv
CHAT_NAME=Onion secured
BOARD_NAME=Private board
SHARE_DIR=/home/USERNAME/onion_share
RECEIVE_DIR=/home/USERNAME/onion_receive
WEBSITE_DIR=/home/USERNAME/onion_website
```

Menus select services only. Edit `config.env` to change names or directories.

## Identity lifecycle

Each service stores its private persistent session under:

```text
~/.torkit/state/SERVICE/
```

The lifecycle commands have distinct meanings:

- `down` stops selected running services and preserves identity.
- `restart` recreates selected running containers and preserves identity.
- `reset` deletes selected identities. Running selections restart with new
  addresses and keys; stopped selections remain stopped and generate new
  credentials on their next start.
- `burn SERVICE...` removes selected containers and private runtime identities;
  it preserves user content directories and configuration. `burn all` selects
  all existing services.
- `nuke` removes all TorKit containers, the Compose network, the TorKit image,
  and all saved runtime identities. It preserves `config.env` and user service
  directories.

`reset` requires confirmation. `burn` and `nuke` require typing the relevant
word (case-insensitively).

## Reporting

`./torkit ps` is an operational Docker view. It shows container name, state,
health, restart count, uptime, and image. It never displays onion credentials.

`./torkit status` is the private access dashboard. It shows each running
service's configured name or directory, onion address, and authorization key.
Its output is sensitive.

## Security and readiness

Routine container logs redact onion access information. TorKit verifies `0700`
directory permissions and `0600` file permissions and fails closed when the
filesystem does not honor them.

No service publishes a clearnet host port. Onion client authorization remains
enabled because containers never pass `--public`.

Normal starts rebuild only when the image is missing or stale. A service is
reported ready only after Tor publishes the onion service and the protected
access file exists.

## Unattended operations

The interactive launcher remains the default for local use. Automation should use
`./torkit ensure SERVICE...` (service names: `chat`, `share`, `receive`,
`website`, `board`; `all` selects every service). `ensure` builds a missing/stale
image and reconciles missing, unhealthy, or reconfigured selected services.
It prints no addresses or client authorization keys. Repeated runs with current
images and ready services do not recreate containers.

`./torkit health --json` emits non-sensitive container state, health, restart
counts, and readiness for all five services. The container probe checks for a
running non-zombie CLI child and a protected, well-formed access file; it is
**not** proof of end-to-end onion reachability. A separate authorized Tor
client probe is needed for external availability monitoring.

`~/.torkit/state/SERVICE/session.json` contains private onion identity material.
Back up the session files and relevant receiving directories using encrypted,
off-host storage. Do not collect these files in CI artifacts or include the
output of `./torkit status` in automation logs. A corrupted existing
persistent session now fails closed instead of silently generating a new
identity.

## Operator console

Run `./torkit operator` from a private interactive shell as the dedicated
`torkit` account. It binds to `127.0.0.1:8769` on the VPS, prints a
temporary login token, and closes on Ctrl-C. The operator console never
publishes on a Tor onion address or the VPS public network interface.

Forward it from a separate SSH terminal on the administrator workstation:

```bash
ssh -N -p 4222 -L 127.0.0.1:8769:127.0.0.1:8769 hetz-1@YOUR_VPS_IP
```

Visit `http://127.0.0.1:8769` in the local browser and enter the temporary
token from the operator terminal. The red Burn button destroys the selected
service's private runtime identity; Nuke TorKit destroys all runtime
identities and the local container image. Both use a single popup: type
`burn` or `nuke` in any capitalization, then Enter or click the button.
Escape cancels. The server independently checks credentials and the
confirmation word before invoking a destructive action.

The dashboard separates active containers from services available to launch.
`Launch new` starts a service without a saved identity; `Resume` restores its
previous onion identity, and `Repair` reconciles an unhealthy running service.
`Stop` preserves identity. An active service displays `BURN`; a stopped service
with saved identity displays `Clear saved state`, which performs the same
service-scoped burn after the one-word confirmation. An absent service without
saved state cannot be burned and has only `Launch new`.

There is currently one instance per service type, not multiple simultaneous
chat rooms; launching Chat does not replace an existing room.

The `Access` button appears only for ready services. It fetches the onion address
and client authorization key on demand through a separately authenticated
`POST /api/access` endpoint (not through `/api/status`). The key is masked
until revealed; either value can be copied. Closing the panel clears the
displayed values. Browser responses are `Cache-Control: no-store`, but copied
values remain on the operator workstation clipboard until replaced. Never
paste authorization keys into tickets, logs, or public chats.

This is an operator-only interface, separate from the public onion chat UI.
No onion addresses or authorization keys appear in its status API. Board has
independent Launch, Access, Stop, Resume and Burn controls.
Burn and Nuke preserve configured file-share, receive, and website content
directories as well as `config.env`. They do not erase remote backups.

## Persistent Board

Board is a separate, client-authorized onion service with a text-only thread
list, new discussion form and replies. Unlike Chat, posts are stored in SQLite
in the protected Board state directory:

```text
~/.torkit/state/board/board.sqlite3
```

Run `./torkit board` (or `./torkit ensure board`) or click **Launch new** next to Board in the
operator console. Copy its private onion address and client authorization
key using Board's **Access** button. Visitors who possess the client key
can read and post; pseudonyms are self-selected, not verified identities.

The Board database, its onion session and access file survive normal browser
refreshes, Stop, Resume and host restarts. `./torkit burn board` (or the Board
Burn button) stops only Board and removes its private state directory, including
posts and onion identity; other TorKit services and configured file directories
are untouched. Backups and copies held elsewhere are not erased. `nuke` also
removes Board's runtime state.

Board currently shows 50 threads or 250 replies per page with older/newer
navigation and caps locally stored posts at 10,000. Authorized visitors are
not individually rate-limited; limit who receives the Board's client key.

Board supports text and a single attachment per thread or reply. Images (JPG,
PNG, WebP, GIF) preview inline with an explicit Download link; PDF, TXT and ZIP
are download-only. Each file is limited to 10 MiB; the Board attachment total
is capped at 512 MiB. Files and metadata live under the private Board state
folder and are retained across normal restarts. Filenames are randomized on
disk. The image data is not transcoded, so EXIF/GPS information can remain.
There are no user accounts, moderation or end-to-end encryption. Messages are readable by
the service operator and Tor-authorized participants, and persisted in SQLite
on the VPS. Do not assume this is an untraceable or self-destructing message
store. Save a secure off-host backup of Board state if long-term retention is
important; burning Board is intentionally incompatible with that goal.

### Board usability

Board now includes literal-text search across subjects, posts and replies; three
sorting modes (recent activity, newest topics and reply count); paginated search
results and replies; and a separate collapsible new-discussion composer.
Threads display reply counts and the most recent activity time. Participants
can copy thread URLs. Client-side character counters do not save drafts or
messages in browser storage. Search and sorting are server-side; no remote
scripts, analytics or third-party assets are loaded.

The configured limit remains 10,000 stored posts. Search is SQLite literal
case-insensitive matching (ASCII case folding; Unicode searches may require
matching case). Participants' display names are not authenticated.

### Clear Board posts without burning the identity

The operator-only **Clear posts** action is available for a running or stopped
Board with a saved onion identity. It removes all posts and uploaded files. Type `CLEAR` (case-insensitive) in one
confirmation popup. TorKit stops Board if necessary, removes the Board SQLite
files, and resumes it using its existing onion address and client key. Chat,
Share, Receive and Website are unaffected. **Burn Board** still destroys its
onion identity as well as posts. Clear/Burn cannot erase off-host backups or
copies already held by participants.

## Encrypted off-host recovery

TorKit supports encrypted Restic backups to **Amazon S3 or off-host SFTP**. For
small lab backups, Terraform for the private S3 bucket and bucket-scoped IAM
user is provided in `infra/aws-recovery/`. Set `RESTIC_REPOSITORY` to its
`terraform output restic_repository`, plus private `AWS_ACCESS_KEY_ID` and
`AWS_SECRET_ACCESS_KEY` values in `~/.config/torkit/backup.env`. The backup
password itself is NOT included in TorKit backups.

The following SFTP procedure is an alternative to AWS S3. Supply a dedicated
off-host SFTP repository and save the restic password separately.
Install restic on the VPS, set up a restricted SFTP-only account on the remote
backup host, and provision an SSH key for the dedicated torkit user. Verify
and pin the backup server's SSH host key; do not disable host-key checks.
The backup user needs permission to create and modify its remote repository.

In a shell as torkit, configure a strong restic password and private
environment file. Change the SFTP alias, path, and absolute home path:

    install -d -m 700 ~/.config/torkit ~/.ssh
    umask 077
    python3 -c 'import secrets; print(secrets.token_urlsafe(48))' > ~/.config/torkit/restic-password
    chmod 600 ~/.config/torkit/restic-password
    cat > ~/.config/torkit/backup.env <<'EOF'
    RESTIC_REPOSITORY=sftp:my-backup-alias:/srv/restic/torkit
    RESTIC_PASSWORD_FILE=/home/torkit/.config/torkit/restic-password
    EOF
    chmod 600 ~/.config/torkit/backup.env

For AWS S3 instead, see `infra/aws-recovery/README.md`. The S3 bucket is
private, but deleting its Terraform stack with `force_destroy=true` also
deletes the recovery copies. Keep the bucket if you expect disaster recovery.

Set up my-backup-alias in ~/.ssh/config with HostName, User, IdentityFile and
StrictHostKeyChecking yes. Test SFTP access first. Load the environment and
initialize the NEW remote repository exactly once:

    set -a
    . ~/.config/torkit/backup.env
    set +a
    restic --repo "$RESTIC_REPOSITORY" --password-file "$RESTIC_PASSWORD_FILE" init

Do not initialize over an existing repository. Losing the password makes the
encrypted backups unrecoverable.

From /opt/torkit, as torkit with the above variables exported:

    ./torkit backup                # runtime, config and existing content directories
    ./torkit backup --no-content   # runtime/config only; DOES NOT save user content
    ./torkit backup --prune        # also retain 7 daily, 4 weekly, 3 monthly snapshots
    ./torkit snapshots            # list snapshot IDs
    ./torkit backup-check         # verify all remote encrypted repository data
    ./torkit restore SNAPSHOT     # fresh destination; asks for RESTORE confirmation
    ./torkit restore SNAPSHOT --replace  # replace existing state/content explicitly

During backup, TorKit stops active containers while it copies a coherent
snapshot of persistent onion identities, Board SQLite and referenced uploads.
This interruption can be substantial for very large Share/Receive/Website directories; use
--no-content only if those directories are protected by a separate backup.
It resumes those services BEFORE transferring staged data to S3 or SFTP. A failed
stage still attempts to resume the previously active services. Do not run
other TorKit lifecycle commands concurrently with backup or restore. The
recovery lock prevents overlapping recovery commands, not unrelated commands.

Staging temporarily uses unencrypted disk under ~/.torkit/.recovery-* and is
removed at command exit. Protect your VPS filesystem and leave sufficient
space for all selected user content. State-only backups omit Share, Receive
and Website content directories. Backup refuses symlinks and special files.

### Unattended startup and scheduled backups

Compose uses restart: unless-stopped, but the rootless Docker user daemon
must run after reboot. On the VPS as an administrator:

    sudo loginctl enable-linger torkit

Then from the dedicated torkit user session:

    systemctl --user enable --now docker.service
    cd /opt/torkit
    ./torkit startup-check

If there is no user docker.service, configure rootless Docker first; do NOT
enable the privileged system daemon as a substitute. The startup check
reports linger and user-service state; verify the actual behavior by reboot.

Install the included daily backup timer as torkit:

    install -d -m 700 ~/.config/systemd/user
    cp /opt/torkit/container/systemd/torkit-backup.* ~/.config/systemd/user/
    systemctl --user daemon-reload
    systemctl --user enable --now torkit-backup.timer
    systemctl --user list-timers --all | grep torkit
    systemctl --user start torkit-backup.service
    journalctl --user -u torkit-backup.service --no-pager -n 60 | cat

The timer runs a state-only backup (`--no-content`) around 03:17 server-local
time (up to 15 minutes jitter), catches up after downtime, and applies
retention after a successful backup. Board posts/uploads and onion identities
are protected; configured Share/Receive/Website content directories are not.
Its environment file must stay private. Verify remote snapshots and perform
a full backup-check periodically.

### Restore onto a replacement VPS

1. Recreate the torkit account and home path, install rootless Docker and
   restic, deploy the TorKit checkout under /opt/torkit, and reconfigure the
   SSH alias, verified host key and the original restic password.
2. Recreate the parent directories of any external configured Share, Receive
   and Website paths. Restore uses the original absolute paths from config.
   Restore must run before launching new TorKit onions.
3. Export the backup.env variables, list snapshots, choose an ID and run
   ./torkit restore SNAPSHOT. Existing data requires --replace, and all
   TorKit containers must be stopped. The command verifies manifest and
   per-file SHA-256 hashes before confirmation, then asks for RESTORE.
4. Start only the desired services with ./torkit ensure SERVICE..., privately
   verify unchanged onion addresses and Board posts, enable rootless startup,
   test a reboot and create another off-host backup.

Restore stages content beside its destination and rolls back previous local
data if installation fails. It does not securely erase local file blocks or
remote archives. It does not automatically start containers: NEVER run two
live VPS instances with the same restored onion identity concurrently.
