from __future__ import annotations

"""Encrypted off-host restic snapshots of TorKit's runtime and user content."""

from datetime import datetime, timezone
from contextlib import contextmanager
import fcntl
from functools import wraps
import hashlib
import json
import os
from pathlib import Path
import re
import socket
import shutil
import sqlite3
import stat
import subprocess
import sys
import tempfile
import uuid

from . import config as config_module
from .config import chmod, ensure_private_state_dirs, load_config
from .docker import DockerClient
from .model import CONFIG_FILE, SERVICES, STATE_ROOT, TORKIT_DIR, TorKitError
from .runtime import TorKitRuntime

SCHEMA = "torkit-recovery-v1"
SNAPSHOT_RE = re.compile(r"(?:latest|[a-fA-F0-9]{8,64})\Z")


def _settings() -> tuple[list[str], dict[str, str]]:
    repository = os.environ.get("RESTIC_REPOSITORY", "")
    password = os.environ.get("RESTIC_PASSWORD_FILE", "")
    if repository.startswith("sftp:"):
        if not re.fullmatch(r"sftp:[^\s:]+:/[^\s]+", repository):
            raise TorKitError("RESTIC_REPOSITORY must use sftp:host:/absolute/path")
        hostname = repository[5:].split(":/", 1)[0].rsplit("@", 1)[-1]
        if hostname.casefold() in {"localhost", "127.0.0.1", "::1",
                                   socket.gethostname().casefold(),
                                   socket.getfqdn().casefold()}:
            raise TorKitError("backup host cannot be the TorKit VPS itself")
    elif repository.startswith("s3:"):
        # Only accept AWS S3 over TLS, not arbitrary local or insecure endpoints.
        if not re.fullmatch(
            r"s3:s3\.[a-z0-9-]+\.amazonaws\.com/"
            r"[a-z0-9][a-z0-9.-]{1,61}[a-z0-9]/torkit",
            repository,
        ):
            raise TorKitError(
                "RESTIC_REPOSITORY must use s3:s3.REGION.amazonaws.com/BUCKET/torkit"
            )
        if not os.environ.get("AWS_ACCESS_KEY_ID") or not os.environ.get("AWS_SECRET_ACCESS_KEY"):
            raise TorKitError(
                "AWS_ACCESS_KEY_ID and AWS_SECRET_ACCESS_KEY must be configured for S3"
            )
    else:
        raise TorKitError("RESTIC_REPOSITORY must use AWS S3 or off-host SFTP")
    if not password:
        raise TorKitError("set RESTIC_PASSWORD_FILE to a private 0600 password file")
    secret = Path(password).expanduser()
    try:
        info = secret.lstat()
    except OSError as exc:
        raise TorKitError("RESTIC_PASSWORD_FILE cannot be read") from exc
    if (not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid()
            or stat.S_IMODE(info.st_mode) != 0o600):
        raise TorKitError("RESTIC_PASSWORD_FILE must be a regular, non-symlink 0600 file owned by this user")
    if not shutil.which("restic"):
        raise TorKitError("restic is not installed")
    environment = os.environ.copy()
    for key in ("RESTIC_PASSWORD", "RESTIC_PASSWORD_COMMAND",
                "RESTIC_REPOSITORY_FILE", "RESTIC_FROM_PASSWORD",
                "RESTIC_FROM_PASSWORD_FILE", "RESTIC_FROM_PASSWORD_COMMAND"):
        environment.pop(key, None)
    return ["restic", "--repo", repository, "--password-file", str(secret)], environment


def _restic(command: list[str]) -> str:
    prefix, environment = _settings()
    result = subprocess.run(
        [*prefix, *command], env=environment, text=True,
        stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=False,
    )
    if result.returncode:
        raise TorKitError(
            f"restic {command[0]} failed (exit {result.returncode}); "
            "check remote connectivity, initialized repository and encryption password"
        )
    return result.stdout


def _regular_tree(path: Path) -> None:
    if path.is_symlink():
        raise TorKitError(f"unsafe symlink in recovery data: {path}")
    if path.is_file():
        return
    if not path.is_dir():
        raise TorKitError(f"not a regular file or directory: {path}")
    for base, directories, files in os.walk(path, followlinks=False):
        for name in directories + files:
            item = Path(base) / name
            mode = item.lstat().st_mode
            if stat.S_ISLNK(mode):
                raise TorKitError(f"unsafe symlink in recovery data: {item}")
            if not (stat.S_ISDIR(mode) or stat.S_ISREG(mode)):
                raise TorKitError(f"unsafe special file in recovery data: {item}")


def _files(root: Path) -> dict[str, str]:
    results: dict[str, str] = {}
    for path in sorted(root.rglob("*")):
        if not path.is_file() or path == root / "manifest.json":
            continue
        digest = hashlib.sha256()
        with path.open("rb") as stream:
            while block := stream.read(1024 * 1024):
                digest.update(block)
        results[path.relative_to(root).as_posix()] = digest.hexdigest()
    return results


def _stage_payload(payload: Path, configured: dict[str, str],
                   include_content: bool = True) -> None:
    payload.mkdir(mode=0o700)
    _regular_tree(CONFIG_FILE)
    shutil.copy2(CONFIG_FILE, payload / "config.env")
    chmod(payload / "config.env", 0o600)
    _regular_tree(STATE_ROOT)
    shutil.copytree(STATE_ROOT, payload / "state")
    chmod(payload / "state", 0o700)
    board_db = payload / "state" / "board" / "board.sqlite3"
    if board_db.is_file():
        try:
            with sqlite3.connect(board_db.as_uri() + "?mode=ro", uri=True) as connection:
                if connection.execute("PRAGMA quick_check").fetchone() != ("ok",):
                    raise TorKitError("Board SQLite check failed; no backup uploaded")
        except sqlite3.DatabaseError as exc:
            raise TorKitError("Board SQLite check failed; no backup uploaded") from exc
    content: dict[str, str] = {}
    selected_sources: list[Path] = []
    if include_content:
        for service in SERVICES.values():
            if not service.uses_directory:
                continue
            source = Path(configured[service.config_key]).expanduser()
            if not source.is_absolute():
                raise TorKitError(f"{service.config_key} must be absolute")
            resolved = source.resolve()
            if (resolved == TORKIT_DIR or resolved in TORKIT_DIR.parents
                    or TORKIT_DIR in resolved.parents):
                raise TorKitError("configured content overlaps TorKit state")
            if any(resolved == other or resolved in other.parents
                   or other in resolved.parents for other in selected_sources):
                raise TorKitError("configured content directories overlap")
            selected_sources.append(resolved)
            if not source.exists():
                continue
            _regular_tree(source)
            destination = payload / "content" / service.name
            destination.parent.mkdir(exist_ok=True)
            shutil.copytree(source, destination)
            content[service.name] = str(source)
    manifest = {
        "schema": SCHEMA,
        "created_utc": datetime.now(timezone.utc).isoformat(),
        "content": content,
        "files": _files(payload),
    }
    (payload / "manifest.json").write_text(
        json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    chmod(payload / "manifest.json", 0o600)

def _validate_payload(payload: Path) -> dict:
    _regular_tree(payload)
    try:
        manifest = json.loads((payload / "manifest.json").read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise TorKitError("invalid TorKit recovery manifest") from exc
    if (not isinstance(manifest, dict) or manifest.get("schema") != SCHEMA
            or not isinstance(manifest.get("files"), dict)
            or not isinstance(manifest.get("content"), dict)):
        raise TorKitError("unsupported TorKit recovery manifest")
    if not (payload / "state").is_dir() or not (payload / "config.env").is_file():
        raise TorKitError("recovery snapshot is missing configuration or state")
    if _files(payload) != manifest["files"]:
        raise TorKitError("recovery file list or SHA-256 digest mismatch")
    values = config_module._read_config_values(payload / "config.env")
    if config_module.CONFIG_KEYS - values.keys():
        raise TorKitError("recovery configuration is missing service settings")
    valid_content = {service.name for service in SERVICES.values()
                     if service.uses_directory}
    if set(manifest["content"]) - valid_content:
        raise TorKitError("unrecognized content directory in recovery snapshot")
    for name, original in manifest["content"].items():
        if (not isinstance(original, str)
                or not Path(original).is_absolute()
                or original != values[SERVICES[name].config_key]
                or not (payload / "content" / name).is_dir()):
            raise TorKitError("recovery content path does not match configuration")
    if (payload / "content").exists() and {
        item.name for item in (payload / "content").iterdir()
    } != set(manifest["content"]):
        raise TorKitError("unexpected content in recovery snapshot")
    return manifest


def _validate_destination(path: Path, directory: bool) -> None:
    if not path.is_absolute() or not path.parent.is_dir():
        raise TorKitError(f"restore destination needs an existing absolute parent: {path}")
    current = path
    while True:
        if current.is_symlink():
            raise TorKitError(f"unsafe symlink in restore destination: {current}")
        if current == current.parent:
            break
        current = current.parent
    if path.exists() and path.is_dir() != directory:
        raise TorKitError(f"restore target has unexpected type: {path}")


def _remove(path: Path) -> None:
    if path.is_dir():
        shutil.rmtree(path)
    elif path.exists():
        path.unlink()


def _check_targets(payload: Path, manifest: dict, replace: bool) -> list[tuple[Path, Path]]:
    destinations = [(payload / "config.env", CONFIG_FILE),
                    (payload / "state", STATE_ROOT)]
    destinations.extend(
        (payload / "content" / name, Path(path))
        for name, path in manifest["content"].items()
    )
    targets = [target for _, target in destinations]
    for target in targets[2:]:
        if target == TORKIT_DIR or TORKIT_DIR in target.parents:
            raise TorKitError("user content cannot restore inside the TorKit state root")
    for index, target in enumerate(targets):
        for other in targets[index + 1:]:
            if target == other or target in other.parents or other in target.parents:
                raise TorKitError("overlapping restore destinations are not supported")
    for source, target in destinations:
        _validate_destination(target, source.is_dir())
        if target.exists() and not replace:
            raise TorKitError(f"restore target exists: {target}; use --replace to overwrite")
    return destinations


def _install_payload(payload: Path, manifest: dict, replace: bool) -> None:
    """Copy next to each target, rename into place, roll back failed installation."""
    destinations = _check_targets(payload, manifest, replace)
    staged: dict[Path, Path] = {}
    rollback: dict[Path, Path] = {}
    touched: list[Path] = []
    token = uuid.uuid4().hex
    try:
        for source, target in destinations:
            adjacent = target.with_name(f".{target.name}.torkit-new-{token}")
            staged[target] = adjacent
            if source.is_dir():
                shutil.copytree(source, adjacent)
            else:
                shutil.copy2(source, adjacent)
        chmod(staged[CONFIG_FILE], 0o600)
        chmod(staged[STATE_ROOT], 0o700)
        for _, target in destinations:
            touched.append(target)
            if target.exists():
                previous = target.with_name(f".{target.name}.torkit-old-{token}")
                os.replace(target, previous)
                rollback[target] = previous
            os.replace(staged[target], target)
        chmod(CONFIG_FILE, 0o600)
        chmod(STATE_ROOT, 0o700)
        for service in SERVICES.values():
            if service.state_dir.is_dir():
                chmod(service.state_dir, 0o700)
    except Exception:
        for target in reversed(touched):
            _remove(target)
            if target in rollback:
                os.replace(rollback[target], target)
        raise
    finally:
        for path in staged.values():
            _remove(path)
    for previous in rollback.values():
        _remove(previous)


def backup(*, include_content: bool = True, prune: bool = False) -> None:
    _settings()
    configured = load_config()
    ensure_private_state_dirs()
    runtime = TorKitRuntime(configured)
    runtime.require()
    active = [
        service for service in SERVICES.values()
        if runtime.status(service) in {"running", "restarting"}
    ]
    for service in active:
        if runtime.configuration_changed(service):
            raise TorKitError(f"{service.label} configuration changed; reconcile before backup")

    with tempfile.TemporaryDirectory(prefix=".recovery-", dir=TORKIT_DIR) as temporary:
        payload = Path(temporary) / SCHEMA
        try:
            if active:
                # Briefly stop active containers: SQLite and uploaded files must
                # come from one coherent point in time.
                runtime.stop_many(active)
            _stage_payload(payload, configured, include_content=include_content)
        finally:
            # Resume BEFORE uploading. SFTP transfer does not cause downtime.
            if active:
                runtime.ensure(active)

        output = _restic(["backup", "--json", "--group-by", "host",
                          "--tag", SCHEMA, str(payload)])
        snapshot_id = ""
        for raw in output.splitlines():
            try:
                record = json.loads(raw)
            except ValueError:
                continue
            if record.get("message_type") == "summary":
                snapshot_id = record.get("snapshot_id", "")
        print("Encrypted off-host TorKit backup completed.")
        if snapshot_id:
            print(f"Snapshot: {snapshot_id}")
        if prune:
            _restic([
                "forget", "--tag", SCHEMA, "--group-by", "",
                "--keep-daily", "7", "--keep-weekly", "4",
                "--keep-monthly", "3", "--prune",
            ])
            print("Retention applied: 7 daily, 4 weekly, 3 monthly.")


def restore(snapshot: str, *, replace: bool = False) -> None:
    if not SNAPSHOT_RE.fullmatch(snapshot):
        raise TorKitError("snapshot must be 'latest' or an 8-64 digit hex ID")
    _settings()
    TORKIT_DIR.mkdir(parents=True, exist_ok=True)
    chmod(TORKIT_DIR, 0o700)
    # Inspect existing Compose containers without creating a replacement config.
    # DockerClient.compose still needs the complete service environment.
    runtime = TorKitRuntime({service.config_key: service.default
                             for service in SERVICES.values()})
    runtime.require()
    if any(runtime.status(service) in {"running", "restarting", "paused"}
           for service in SERVICES.values()):
        raise TorKitError("stop all TorKit services before restoring")

    with tempfile.TemporaryDirectory(prefix=".recovery-", dir=TORKIT_DIR) as temporary:
        staging = Path(temporary)
        _restic(["restore", snapshot, "--target", str(staging)])
        matches = [
            item.parent for item in staging.rglob("manifest.json")
            if item.parent.name == SCHEMA and item.is_file()
        ]
        if len(matches) != 1:
            raise TorKitError("snapshot does not contain one TorKit recovery payload")
        payload = matches[0]
        manifest = _validate_payload(payload)
        # Check all targets before prompting or removing stale containers.
        _check_targets(payload, manifest, replace)
        if not sys.stdin.isatty():
            raise TorKitError("restore requires an interactive terminal")
        print(f"Restore TorKit snapshot {snapshot} into {TORKIT_DIR}.")
        print("Saved identities, Board posts and selected user content may be replaced.")
        if input("Type RESTORE to continue: ").strip() != "RESTORE":
            raise TorKitError("restore cancelled")
        existing = [
            service for service in SERVICES.values()
            if runtime.status(service) != "absent"
        ]
        if existing:
            runtime.docker.compose(
                "rm", "--force", "--stop",
                *(service.compose_name for service in existing),
            )
        _install_payload(payload, manifest, replace)
    print("TorKit restored. Run './torkit ensure SERVICE...' for desired services.")
    print("Verify restored onion addresses against your private access records.")


def snapshots() -> None:
    output = _restic(["snapshots", "--json", "--tag", SCHEMA])
    try:
        records = json.loads(output)
    except ValueError as exc:
        raise TorKitError("restic returned an invalid snapshot listing") from exc
    for item in records:
        print(f"{item['short_id']:<10} {item['time']}  {item.get('hostname', '-')}")


def backup_check() -> None:
    _restic(["check", "--read-data"])
    print("Restic repository structure and encrypted data verified.")


def startup_check() -> None:
    """Report prerequisites without silently changing privileged host settings."""
    if not shutil.which("systemctl") or not shutil.which("loginctl"):
        raise TorKitError("systemd and loginctl are required for the startup check")
    import pwd

    username = pwd.getpwuid(os.getuid()).pw_name
    checks = [
        (["loginctl", "show-user", username, "--property=Linger", "--value"],
         "yes", "sudo loginctl enable-linger " + username),
        (["systemctl", "--user", "is-enabled", "docker.service"],
         "enabled", "systemctl --user enable --now docker.service"),
        (["systemctl", "--user", "is-active", "docker.service"],
         "active", "systemctl --user start docker.service"),
    ]
    ok = True
    for command, expected, solution in checks:
        result = subprocess.run(command, text=True, capture_output=True, check=False)
        good = result.returncode == 0 and result.stdout.strip() == expected
        print(f"{'PASS' if good else 'FAIL'} {command[0]}: {expected}"
              + ("" if good else f"; fix: {solution}"))
        ok &= good
    if not ok:
        raise TorKitError("rootless Docker boot prerequisites are incomplete")
    client = DockerClient(lambda _build_id: os.environ.copy())
    client.require()
    if not client.rootless:
        raise TorKitError("active Docker daemon is not rootless; check DOCKER_HOST")
    print("PASS Docker daemon: rootless")
    print("Prerequisites passed; verify service availability after an actual reboot.")


@contextmanager
def _exclusive():
    """Prevent simultaneous backup/restore or overlapping backup timers."""
    TORKIT_DIR.mkdir(parents=True, exist_ok=True)
    chmod(TORKIT_DIR, 0o700)
    lock = TORKIT_DIR / ".recovery.lock"
    flags = os.O_RDWR | os.O_CREAT
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    try:
        descriptor = os.open(lock, flags, 0o600)
    except OSError as exc:
        raise TorKitError("cannot open the private recovery lock") from exc
    try:
        info = os.fstat(descriptor)
        if info.st_uid != os.getuid() or stat.S_IMODE(info.st_mode) != 0o600:
            raise TorKitError("recovery lock has unsafe ownership or permissions")
        try:
            fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            raise TorKitError("another TorKit backup or restore is running") from exc
        yield
    finally:
        os.close(descriptor)


def _with_lock(function):
    @wraps(function)
    def wrapped(*args, **kwargs):
        with _exclusive():
            return function(*args, **kwargs)
    return wrapped


backup = _with_lock(backup)
restore = _with_lock(restore)
