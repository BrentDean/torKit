from __future__ import annotations

from datetime import datetime, timezone
import os
from pathlib import Path
import shutil
import sys
import time
from typing import Iterable

from .config import chmod
from .docker import DockerClient
from .model import (
    CONFIG_FILE,
    IMAGE,
    SERVICES,
    STATE_ROOT,
    Service,
    TorKitError,
)
from .process import run


class TorKitRuntime:
    def __init__(self, config: dict[str, str]) -> None:
        self.config = config
        self.docker = DockerClient(self._environment)

    def require(self) -> None:
        self.docker.require()

    def value(self, service: Service) -> str:
        return self.config[service.config_key]

    def host_path(self, service: Service) -> str:
        path = Path(self.value(service)).expanduser()
        if not path.is_absolute():
            raise TorKitError(
                f"{service.config_key} must be an absolute path in {CONFIG_FILE}"
            )
        return str(path)

    def _environment(self, build_id: str) -> dict[str, str]:
        env = os.environ.copy()
        # Rootless Docker maps container UID/GID 0 to the unprivileged host
        # daemon owner; using the host UID numerically would map to a subuid.
        container_uid = 0 if self.docker.rootless else os.getuid()
        container_gid = 0 if self.docker.rootless else os.getgid()
        env.update(
            TORKIT_IMAGE=IMAGE,
            TORKIT_BUILD_ID=build_id,
            TORKIT_CONTAINER_UID=str(container_uid),
            TORKIT_CONTAINER_GID=str(container_gid),
        )

        for service in SERVICES.values():
            configured_value = (
                self.host_path(service) if service.uses_directory else self.value(service)
            )
            env[service.value_env] = configured_value
            env[service.state_env] = str(service.state_dir)

        return env

    def status(self, service: Service) -> str:
        return self.docker.status(service)

    def active_value(self, service: Service) -> str:
        """Return the value actually used by the running container."""
        if self.status(service) != "running":
            return self.value(service)
        if service.mount_target:
            active = self.docker.mount_source(service, service.mount_target)
        else:
            active = self.docker.environment_value(service, service.value_env)
        return active or self.value(service)

    def configuration_changed(self, service: Service) -> bool:
        if self.status(service) != "running":
            return False
        if service.uses_directory:
            return Path(self.active_value(service)).resolve() != Path(
                self.host_path(service)
            ).resolve()
        return self.active_value(service) != self.value(service)

    def access(self, service: Service) -> dict[str, str]:
        values: dict[str, str] = {}
        try:
            lines = service.access_file.read_text(encoding="utf-8").splitlines()
        except OSError:
            return values

        for line in lines:
            if "=" not in line:
                continue
            key, value = line.split("=", 1)
            if key in {"ONION_ADDRESS", "ACCESS_KEY"}:
                values[key] = value
        return values

    def ready(self, service: Service) -> bool:
        info = self.docker.container_info(service)
        access = self.access(service)
        return (
            info is not None
            and info.state == "running"
            and info.health == "healthy"
            and bool(access.get("ONION_ADDRESS"))
            and bool(access.get("ACCESS_KEY"))
        )

    def uptime(self, service: Service) -> str:
        return self._uptime_from_started(
            self.docker.started_at(service),
            self.status(service),
        )

    def prepare_directory(self, service: Service) -> None:
        if not service.uses_directory:
            return

        configured = Path(self.host_path(service))
        if not configured.exists():
            try:
                configured.mkdir(parents=True)
            except PermissionError:
                if sys.stdin.isatty() and sys.stdout.isatty() and shutil.which("sudo"):
                    print(f"Creating {configured} requires administrator permission.")
                    run(
                        [
                            "sudo",
                            "install",
                            "-d",
                            "-o",
                            str(os.getuid()),
                            "-g",
                            str(os.getgid()),
                            "-m",
                            "0750",
                            str(configured),
                        ],
                        capture=False,
                    )
                else:
                    raise TorKitError(
                        f"directory does not exist: {configured}; edit {CONFIG_FILE}"
                    )

        if not configured.is_dir():
            raise TorKitError(f"not a directory: {configured}")
        if service.directory_access == "read" and not os.access(configured, os.R_OK):
            raise TorKitError(f"directory is not readable: {configured}")
        if service.directory_access == "write" and not os.access(configured, os.W_OK):
            raise TorKitError(f"directory is not writable: {configured}")

    @staticmethod
    def _remove_access_files(services: Iterable[Service]) -> None:
        for service in services:
            service.access_file.unlink(missing_ok=True)

    def _recreate_services(self, services: Iterable[Service]) -> None:
        selected = list(services)
        if not selected:
            return

        for service in selected:
            self.prepare_directory(service)
        self.docker.ensure_image()
        self._remove_access_files(selected)
        self.docker.compose(
            "up",
            "--detach",
            "--no-build",
            "--force-recreate",
            *(service.compose_name for service in selected),
        )

    def wait_ready(self, service: Service, timeout: int = 180) -> None:
        started = time.monotonic()
        deadline = started + timeout
        last_progress: tuple[str, str] | None = None
        next_report = 0

        while time.monotonic() < deadline:
            if self.ready(service):
                return

            status = self.status(service)
            if status in {"exited", "dead", "absent"}:
                self._print_failure_logs(service, f"container is {status}")

            # Report Docker health transitions and a heartbeat every 15 seconds.
            # Never include access.env contents or credentials in progress output.
            health = self.docker.health(service)
            progress = (status, health)
            elapsed = int(time.monotonic() - started)
            if progress != last_progress or elapsed >= next_report:
                print(
                    f"  {service.label}: container={status}, health={health}, "
                    f"elapsed={elapsed}s",
                    flush=True,
                )
                last_progress = progress
                next_report = elapsed + 15

            time.sleep(2)
        self._print_failure_logs(
            service, f"did not become ready within {timeout} seconds"
        )

    def _print_failure_logs(self, service: Service, message: str) -> None:
        print(f"\n{service.label} failed. Recent logs:", file=sys.stderr)
        try:
            self.docker.logs(service)
        except TorKitError:
            pass
        raise TorKitError(f"{service.label} {message}")

    def _service_access(self, service: Service) -> None:
        status = self.status(service)
        print(f"\n{service.label}")
        field = "Name" if not service.uses_directory else "Directory"
        active = self.active_value(service)
        print(f"  {field:<13} {active}")
        if self.configuration_changed(service):
            print(f"  {'Configured':<13} {self.value(service)}")
            print(f"  {'Status':<13} restart required")
        else:
            print(f"  {'Status':<13} {status}")
        if status == "running":
            access = self.access(service)
            print(f"  {'Uptime':<13} {self.uptime(service)}")
            print(f"  {'Onion address':<13} {access.get('ONION_ADDRESS', '-')}")
            print(f"  {'Access key':<13} {access.get('ACCESS_KEY', '-')}")

    def start(self, selected: Iterable[Service]) -> None:
        selected = list(selected)
        targets = [service for service in selected if not self.ready(service)]
        for service in selected:
            if service not in targets:
                print(f"{service.label} is already running; skipping.")
        if not targets:
            return

        print("Starting TorKit services...")
        self._recreate_services(targets)
        for service in targets:
            self.wait_ready(service)
            print(f"Ready: {service.label}")
            self._service_access(service)

    def ensure(self, selected: Iterable[Service]) -> None:
        """Idempotently reconcile specified services for unattended deployments."""
        selected = list(selected)
        if not selected:
            raise TorKitError("ensure requires at least one service")

        image_stale = not self.docker.image_is_current()
        targets = [
            service for service in selected
            if image_stale or not self.ready(service)
            or self.configuration_changed(service)
        ]
        for service in selected:
            if service not in targets:
                print(f"{service.label}: already ready (image and configuration current).")

        if not targets:
            return

        for service in targets:
            print(f"{service.label}: reconciling container and image...", flush=True)

        self._recreate_services(targets)
        for service in targets:
            print(
                f"{service.label}: waiting for Docker health and Tor publication...",
                flush=True,
            )
            started = time.monotonic()
            self.wait_ready(service)
            elapsed = int(time.monotonic() - started)
            print(
                f"Ready: {service.label} (healthy, onion access recorded; "
                f"waited {elapsed}s)"
            )

    def health_snapshot(self) -> dict[str, dict[str, str | bool]]:
        """Health reporting only: never include service addresses or access keys."""
        snapshot: dict[str, dict[str, str | bool]] = {}
        for service in SERVICES.values():
            info = self.docker.container_info(service)
            snapshot[service.name] = {
                "state": info.state if info else "absent",
                "health": info.health if info else "absent",
                "ready": self.ready(service) if info else False,
                "restarts": info.restart_count if info else "0",
                # Presence only; never expose private session contents or paths.
                "identity_saved": (service.state_dir / "session.json").is_file(),
            }
        return snapshot

    def stop_many(self, selected: Iterable[Service]) -> None:
        selected = list(selected)
        if not selected:
            print("No services stopped.")
            return

        self.docker.compose(
            "stop",
            *(service.compose_name for service in selected),
        )
        for service in selected:
            print(f"Stopped: {service.label}")
        print("Saved onion identities were preserved.")

    def restart_many(self, selected: Iterable[Service]) -> None:
        selected = [
            service for service in selected if self.status(service) == "running"
        ]
        if not selected:
            print("No services restarted.")
            return

        print("Restarting TorKit services...")
        self._recreate_services(selected)
        for service in selected:
            self.wait_ready(service)
            print(f"Restarted: {service.label}")
        print("Saved onion identities were preserved.")

    @staticmethod
    def _clear_service_state(service: Service) -> None:
        if service.state_dir.exists():
            shutil.rmtree(service.state_dir)
        service.state_dir.mkdir(parents=True, exist_ok=True)
        chmod(service.state_dir, 0o700)

    def reset_identities(self, selected: Iterable[Service]) -> None:
        selected = list(selected)
        if not selected:
            print("No services reset.")
            return

        statuses = {service.name: self.status(service) for service in selected}
        running = [
            service for service in selected if statuses[service.name] == "running"
        ]
        existing = [
            service for service in selected if statuses[service.name] != "absent"
        ]

        if existing:
            self.docker.compose(
                "rm",
                "--force",
                "--stop",
                *(service.compose_name for service in existing),
            )

        for service in selected:
            self._clear_service_state(service)
            print(f"Reset identity: {service.label}")

        if running:
            print()
            self.start(running)
        else:
            print("New addresses and keys will be created the next time services start.")

    def burn(self, selected: Iterable[Service]) -> None:
        """Destroy only selected private runtime state; preserve user directories."""
        selected = list(selected)
        if not selected:
            raise TorKitError("burn requires at least one service")
        existing = [
            service for service in selected
            if self.status(service) != "absent"
        ]
        if existing:
            self.docker.compose(
                "rm", "--force", "--stop",
                *(service.compose_name for service in existing),
            )
        for service in selected:
            self._clear_service_state(service)
            print(f"Burned: {service.label}")
        print("Selected onion identities and private runtime state destroyed.")

    def clear_board(self) -> bool:
        """Erase Board posts while preserving its onion identity and other state.

        Returns whether Board was active and therefore automatically resumed.
        This is intentionally not a secure erasure of backups or filesystem blocks.
        """
        board = SERVICES["board"]
        if not (board.state_dir / "session.json").is_file():
            raise TorKitError("Board has no saved identity to preserve")

        active = self.status(board) in {"running", "restarting"}
        if active:
            self.stop_many([board])

        # Check the upload location before deleting any state.
        uploads = board.state_dir / "uploads"
        if uploads.is_symlink() or (uploads.exists() and not uploads.is_dir()):
            raise TorKitError("Board upload path is unsafe; refusing to clear")

        # The container is stopped before touching SQLite or uploaded files.
        for suffix in ("", "-journal", "-wal", "-shm"):
            (board.state_dir / ("board.sqlite3" + suffix)).unlink(missing_ok=True)
        if uploads.exists():
            shutil.rmtree(uploads)

        print("Board posts and attachments cleared; onion identity preserved.")
        if active:
            self.ensure([board])
        return active

    def nuke(self) -> None:
        self.docker.compose("down", "--volumes", "--remove-orphans")
        self.docker.remove_image()

        if STATE_ROOT.exists():
            shutil.rmtree(STATE_ROOT)

        print("TorKit services destroyed.")

    @staticmethod
    def _uptime_from_started(started: str, state: str) -> str:
        if state != "running" or not started:
            return "-"
        try:
            start = datetime.fromisoformat(started.replace("Z", "+00:00"))
            seconds = max(
                0,
                int((datetime.now(timezone.utc) - start).total_seconds()),
            )
        except ValueError:
            return "-"

        hours, remainder = divmod(seconds, 3600)
        minutes = remainder // 60
        return f"{hours}h {minutes}m" if hours else f"{minutes}m"

    def ps(self) -> None:
        print("TORKit CONTAINERS\n")
        print(
            f"{'SERVICE':<9} {'CONTAINER':<24} {'STATE':<10} {'HEALTH':<10} "
            f"{'RESTARTS':<9} {'UPTIME':<10} IMAGE"
        )
        print(
            f"{'---------':<9} {'------------------------':<24} "
            f"{'----------':<10} {'----------':<10} {'---------':<9} "
            f"{'----------':<10} -----"
        )

        count = 0
        for service in SERVICES.values():
            info = self.docker.container_info(service)
            if info is None:
                continue

            health = info.health
            if info.state not in {"running", "restarting"} or health == "none":
                health = "-"

            uptime = self._uptime_from_started(info.started_at, info.state)
            print(
                f"{service.name:<9} {info.name[:24]:<24} "
                f"{info.state:<10} {health:<10} {info.restart_count:<9} "
                f"{uptime:<10} {info.image}"
            )
            count += 1

        if count == 0:
            print("No TorKit containers have been created.")

    def dashboard(self) -> None:
        print("TorKit SERVICE STATUS")
        count = 0
        for service in SERVICES.values():
            if self.status(service) != "running":
                continue
            self._service_access(service)
            count += 1

        if count == 0:
            print("\nNo TorKit services are running.")
            return

        print("\nAccess keys are private. Share them only with intended users.")
