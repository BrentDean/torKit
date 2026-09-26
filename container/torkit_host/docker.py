from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
import hashlib
import json
import shutil
import subprocess

from .model import (
    COMPOSE_FILE,
    IMAGE,
    PROJECT_NAME,
    ROOT,
    SERVICES,
    Service,
    TorKitError,
)
from .process import run

EnvironmentFactory = Callable[[str], dict[str, str]]


@dataclass(frozen=True)
class ContainerInfo:
    name: str
    state: str
    health: str
    restart_count: str
    started_at: str
    image: str


class DockerClient:
    def __init__(self, environment: EnvironmentFactory) -> None:
        self._environment = environment
        self.build_id = self._fingerprint()
        self.rootless = False

    def require(self) -> None:
        if shutil.which("docker") is None:
            raise TorKitError("docker is not installed")
        run(["docker", "compose", "version"])
        result = run(["docker", "info", "--format", "{{json .SecurityOptions}}"])
        try:
            options = json.loads(result.stdout or "")
        except (TypeError, ValueError) as exc:
            raise TorKitError("could not identify Docker daemon security mode") from exc
        if not isinstance(options, list) or not all(
            isinstance(option, str) for option in options
        ):
            raise TorKitError("unexpected Docker daemon security options")
        self.rootless = "name=rootless" in options

    @staticmethod
    def _fingerprint() -> str:
        files = [
            ROOT / "Dockerfile",
            ROOT / "container" / "entrypoint.sh",
            ROOT / "container" / "run-service.py",
            ROOT / "container" / "healthcheck.py",
            ROOT / "cli" / "pyproject.toml",
            ROOT / "cli" / "poetry.lock",
        ]
        files.extend(
            path
            for path in (ROOT / "cli" / "onionshare_cli").rglob("*")
            if path.is_file()
            and "__pycache__" not in path.parts
            and path.suffix != ".pyc"
        )

        digest = hashlib.sha256()
        for path in sorted(files):
            digest.update(str(path.relative_to(ROOT)).encode())
            digest.update(b"\0")
            digest.update(path.read_bytes())
            digest.update(b"\0")
        return digest.hexdigest()

    def compose(
        self,
        *arguments: str,
        check: bool = True,
    ) -> subprocess.CompletedProcess[str]:
        command = [
            "docker",
            "compose",
            "--project-directory",
            str(ROOT),
            "--project-name",
            PROJECT_NAME,
            "--file",
            str(COMPOSE_FILE),
        ]
        for service in SERVICES.values():
            command.extend(("--profile", service.compose_name))
        command.extend(arguments)

        return run(
            command,
            env=self._environment(self.build_id),
            check=check,
        )

    def _image_build_id(self) -> str:
        result = run(
            [
                "docker",
                "image",
                "inspect",
                "--format",
                '{{index .Config.Labels "org.torkit.build-id"}}',
                IMAGE,
            ],
            check=False,
        )
        return (result.stdout or "").strip() if result.returncode == 0 else ""

    def build(self) -> None:
        print("Building TorKit image...")
        self.compose("build")
        print("TorKit image ready.")

    def image_is_current(self) -> bool:
        return self._image_build_id() == self.build_id

    def ensure_image(self) -> None:
        if not self.image_is_current():
            self.build()

    def remove_image(self) -> None:
        run(["docker", "image", "rm", "--force", IMAGE], check=False)

    def _container_id(self, service: Service) -> str:
        result = self.compose(
            "ps",
            "--all",
            "--quiet",
            service.compose_name,
            check=False,
        )
        lines = (result.stdout or "").strip().splitlines()
        return lines[0] if lines else ""

    def _inspect(self, service: Service, template: str, default: str) -> str:
        container_id = self._container_id(service)
        if not container_id:
            return default

        result = run(
            ["docker", "inspect", "--format", template, container_id],
            check=False,
        )
        if result.returncode != 0:
            return default
        return (result.stdout or "").strip()

    def status(self, service: Service) -> str:
        return self._inspect(service, "{{.State.Status}}", "absent")

    def health(self, service: Service) -> str:
        return self._inspect(
            service,
            "{{if .State.Health}}{{.State.Health.Status}}{{else}}none{{end}}",
            "absent",
        )

    def environment_value(self, service: Service, name: str) -> str:
        """Read a non-secret configured container environment value."""
        output = self._inspect(
            service, "{{range .Config.Env}}{{println .}}{{end}}", ""
        )
        prefix = f"{name}="
        for line in output.splitlines():
            if line.startswith(prefix):
                return line[len(prefix):]
        return ""

    def mount_source(self, service: Service, destination: str) -> str:
        template = (
            "{{range .Mounts}}"
            f"{{{{if eq .Destination \"{destination}\"}}}}"
            "{{.Source}}"
            "{{end}}"
            "{{end}}"
        )
        return self._inspect(service, template, "")

    def started_at(self, service: Service) -> str:
        return self._inspect(service, "{{.State.StartedAt}}", "")

    def logs(self, service: Service, follow: bool = False) -> int:
        container_id = self._container_id(service)
        if not container_id:
            raise TorKitError(f"{service.label} has not been created")

        command = ["docker", "logs", "--tail", "200"]
        if follow:
            command.append("--follow")
        command.append(container_id)
        return run(command, capture=False, check=False).returncode

    def container_info(self, service: Service) -> ContainerInfo | None:
        template = (
            "{{.Name}}|{{.State.Status}}|"
            "{{if .State.Health}}{{.State.Health.Status}}{{else}}none{{end}}|"
            "{{.RestartCount}}|{{.State.StartedAt}}|{{.Config.Image}}"
        )
        summary = self._inspect(service, template, "")
        if not summary:
            return None

        fields = summary.split("|", 5)
        if len(fields) != 6:
            return None

        name, state, health, restart_count, started_at, image = fields
        return ContainerInfo(
            name=name.lstrip("/") or "-",
            state=state,
            health=health,
            restart_count=restart_count,
            started_at=started_at,
            image=image,
        )
