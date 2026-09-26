from __future__ import annotations

from collections import OrderedDict
from dataclasses import dataclass
import os
from pathlib import Path
from typing import Literal

ROOT = Path(__file__).resolve().parents[2]
STATE_DIR_OVERRIDDEN = "TORKIT_STATE_DIR" in os.environ
LEGACY_TORKIT_DIR = Path(
    os.environ.get("TORKIT_LEGACY_STATE_DIR", ROOT / ".torkit")
).expanduser().resolve()
TORKIT_DIR = Path(
    os.environ.get("TORKIT_STATE_DIR", Path.home() / ".torkit")
).expanduser().resolve()
CONFIG_FILE = TORKIT_DIR / "config.env"
STATE_ROOT = TORKIT_DIR / "state"
COMPOSE_FILE = ROOT / "compose.yaml"
IMAGE = os.environ.get("TORKIT_IMAGE", "torkit:local")
PROJECT_NAME = os.environ.get("TORKIT_PROJECT_NAME", "torkit")

DirectoryAccess = Literal["read", "write"]


class TorKitError(RuntimeError):
    pass


@dataclass(frozen=True)
class Service:
    name: str
    compose_name: str
    label: str
    config_key: str
    default: str
    value_env: str
    state_env: str
    mount_target: str | None = None
    directory_access: DirectoryAccess | None = None

    @property
    def state_dir(self) -> Path:
        return STATE_ROOT / self.name

    @property
    def access_file(self) -> Path:
        return self.state_dir / "access.env"

    @property
    def uses_directory(self) -> bool:
        return self.directory_access is not None


SERVICES: OrderedDict[str, Service] = OrderedDict(
    (
        (
            "chat",
            Service(
                name="chat",
                compose_name="chat",
                label="Chat",
                config_key="CHAT_NAME",
                default="Onion secured",
                value_env="TORKIT_CHAT_TITLE",
                state_env="TORKIT_CHAT_STATE",
            ),
        ),
        (
            "board",
            Service(
                name="board",
                compose_name="board",
                label="Board",
                config_key="BOARD_NAME",
                default="Private board",
                value_env="TORKIT_BOARD_TITLE",
                state_env="TORKIT_BOARD_STATE",
            ),
        ),
        (
            "share",
            Service(
                name="share",
                compose_name="fileshare",
                label="Share",
                config_key="SHARE_DIR",
                default=str(Path.home() / "onion_share"),
                value_env="TORKIT_FILESHARE_PATH",
                state_env="TORKIT_FILESHARE_STATE",
                mount_target="/srv/torkit/share",
                directory_access="read",
            ),
        ),
        (
            "receive",
            Service(
                name="receive",
                compose_name="receive",
                label="Receive",
                config_key="RECEIVE_DIR",
                default=str(Path.home() / "onion_receive"),
                value_env="TORKIT_RECEIVE_PATH",
                state_env="TORKIT_RECEIVE_STATE",
                mount_target="/srv/torkit/receive",
                directory_access="write",
            ),
        ),
        (
            "website",
            Service(
                name="website",
                compose_name="website",
                label="Website",
                config_key="WEBSITE_DIR",
                default=str(Path.home() / "onion_website"),
                value_env="TORKIT_WEBSITE_PATH",
                state_env="TORKIT_WEBSITE_STATE",
                mount_target="/srv/torkit/website",
                directory_access="read",
            ),
        ),
    )
)
CONFIG_KEYS = {service.config_key for service in SERVICES.values()}
