from __future__ import annotations

from pathlib import Path
import stat

from .model import (
    CONFIG_FILE,
    CONFIG_KEYS,
    LEGACY_TORKIT_DIR,
    SERVICES,
    STATE_DIR_OVERRIDDEN,
    STATE_ROOT,
    TORKIT_DIR,
    Service,
    TorKitError,
)


def chmod(path: Path, mode: int) -> None:
    """Apply and verify private permissions; fail closed if the filesystem ignores them."""
    try:
        path.chmod(mode)
        actual = stat.S_IMODE(path.stat().st_mode)
    except OSError as exc:
        raise TorKitError(f"cannot secure {path}: {exc}") from exc

    if actual != mode:
        raise TorKitError(
            f"cannot secure {path}: requested {mode:o}, filesystem reports {actual:o}"
        )


def _strip_quotes(value: str) -> str:
    if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
        return value[1:-1]
    return value


def _read_config_values(path: Path) -> dict[str, str]:
    values: dict[str, str] = {}
    if not path.is_file():
        return values

    for number, raw in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        if not raw.strip() or raw.lstrip().startswith("#"):
            continue
        if "=" not in raw:
            raise TorKitError(f"invalid configuration at {path}:{number}")
        key, value = raw.split("=", 1)
        key = "".join(key.split())
        value = _strip_quotes(value.rstrip("\r"))
        if key not in CONFIG_KEYS:
            raise TorKitError(f"unknown setting {key} in {path}")
        if not value:
            raise TorKitError(f"{key} cannot be empty in {path}")
        values[key] = value
    return values


def _migration_roots() -> tuple[Path, ...]:
    roots = [TORKIT_DIR]
    if not STATE_DIR_OVERRIDDEN and LEGACY_TORKIT_DIR != TORKIT_DIR:
        roots.append(LEGACY_TORKIT_DIR)
    return tuple(roots)


def _legacy_value(service: Service) -> str | None:
    direct_name = {
        "board": None,
        "chat": "chat-name",
        "share": "share-path",
        "receive": "receive-path",
        "website": "website-path",
    }[service.name]

    if direct_name is None:
        return None

    for root in _migration_roots():
        direct = root / direct_name
        if direct.is_file():
            lines = direct.read_text(encoding="utf-8").splitlines()
            if lines and lines[0]:
                return lines[0]

        legacy = {
            "share": ("fileshare.env", "TORKIT_FILESHARE_PATH"),
            "receive": ("receive.env", "TORKIT_RECEIVE_PATH"),
            "website": ("website.env", "TORKIT_WEBSITE_PATH"),
        }.get(service.name)
        if not legacy:
            continue

        filename, key = legacy
        path = root / filename
        if not path.is_file():
            continue
        for raw in path.read_text(encoding="utf-8").splitlines():
            if raw.startswith(f"{key}="):
                value = _strip_quotes(raw.split("=", 1)[1])
                return value or None
    return None


def _create_initial_config() -> None:
    TORKIT_DIR.mkdir(parents=True, exist_ok=True)
    chmod(TORKIT_DIR, 0o700)

    values = {service.config_key: service.default for service in SERVICES.values()}

    # Migrate the previous repo-local config when moving to ~/.torkit.
    if not STATE_DIR_OVERRIDDEN and LEGACY_TORKIT_DIR != TORKIT_DIR:
        values.update(_read_config_values(LEGACY_TORKIT_DIR / "config.env"))

    for service in SERVICES.values():
        if service.config_key not in values or values[service.config_key] == service.default:
            migrated = _legacy_value(service)
            if migrated:
                values[service.config_key] = migrated

    temporary = TORKIT_DIR / ".config.new"
    temporary.write_text(
        "".join(
            f"{key}={values[key]}\n"
            for key in (service.config_key for service in SERVICES.values())
        ),
        encoding="utf-8",
    )
    chmod(temporary, 0o600)
    temporary.replace(CONFIG_FILE)
    chmod(CONFIG_FILE, 0o600)

    # Remove only obsolete files from the active state directory. The old repo-local
    # directory remains untouched as a migration backup until the user removes it.
    for old_name in (
        "chat-name",
        "share-path",
        "receive-path",
        "website-path",
        "fileshare.env",
        "receive.env",
        "website.env",
    ):
        try:
            (TORKIT_DIR / old_name).unlink()
        except FileNotFoundError:
            pass


def load_config() -> dict[str, str]:
    if not CONFIG_FILE.is_file():
        _create_initial_config()
    chmod(CONFIG_FILE, 0o600)

    config = _read_config_values(CONFIG_FILE)
    missing = sorted(CONFIG_KEYS - config.keys())
    if missing == ["BOARD_NAME"]:
        # Upgrade existing four-service deployments without overwriting private config.
        config["BOARD_NAME"] = SERVICES["board"].default
        temporary = TORKIT_DIR / ".config.new"
        temporary.write_text(
            CONFIG_FILE.read_text(encoding="utf-8").rstrip("\n")
            + f"\nBOARD_NAME={config['BOARD_NAME']}\n",
            encoding="utf-8",
        )
        chmod(temporary, 0o600)
        temporary.replace(CONFIG_FILE)
        chmod(CONFIG_FILE, 0o600)
        missing = []
    if missing:
        raise TorKitError(f"missing {', '.join(missing)} in {CONFIG_FILE}")
    return config


def ensure_private_state_dirs() -> None:
    TORKIT_DIR.mkdir(parents=True, exist_ok=True)
    STATE_ROOT.mkdir(parents=True, exist_ok=True)
    chmod(TORKIT_DIR, 0o700)
    chmod(STATE_ROOT, 0o700)
    for service in SERVICES.values():
        service.state_dir.mkdir(parents=True, exist_ok=True)
        chmod(service.state_dir, 0o700)


def normalize_service(value: str) -> Service:
    if value == "fileshare":
        value = "share"
    if value == "messageboard":
        value = "board"
    try:
        return SERVICES[value]
    except KeyError as exc:
        raise TorKitError(f"unknown service: {value or '<missing>'}") from exc
