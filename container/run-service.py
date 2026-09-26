#!/usr/bin/env python3
"""Run torkit-cli, persist its onion identity, and keep secrets out of logs."""

from __future__ import annotations

import json
import os
from pathlib import Path
import re
import signal
import subprocess
import sys
import tempfile
from typing import Any

ONION_RE = re.compile(r"(?:https?://)?[a-z2-7]{56}\.onion\b")
KEY_RE = re.compile(r"Private key:\s*([A-Z2-7]+)")
SECRET_VALUE_RE = re.compile(
    r"(?P<label>\b(?:Private key|(?:onion\.)?(?:private_key|"
    r"client_auth_priv_key|client_auth_pub_key)|ACCESS_KEY|service_id)\b\s*[:=]\s*)"
    r"(?:\"[^\"]*\"|'[^']*'|[^\s,}]+)",
    re.IGNORECASE,
)


def redact_output(line: str) -> str:
    """Redact secrets at the container log boundary."""
    return ONION_RE.sub(
        "[REDACTED ONION ADDRESS]",
        SECRET_VALUE_RE.sub(r"\g<label>[REDACTED]", line),
    )



def atomic_write(path: Path, content: str, mode: int = 0o600) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    os.chmod(path.parent, 0o700)
    fd, tmp_name = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    try:
        os.fchmod(fd, mode)
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(tmp_name, path)
        os.chmod(path, mode)
    except Exception:
        try:
            os.unlink(tmp_name)
        except FileNotFoundError:
            pass
        raise


def update_persistent_session(path: Path) -> None:
    """Refresh configured title/path while preserving onion keys."""
    if not path.exists():
        return

    try:
        data: dict[str, Any] = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise RuntimeError(
            "persistent session is unreadable; refusing to rotate onion identity"
        ) from exc

    if not isinstance(data, dict) or not isinstance(data.get("onion"), dict):
        raise RuntimeError("persistent session is invalid; refusing to rotate onion identity")
    if not data["onion"].get("private_key"):
        raise RuntimeError("persistent session has no onion identity; refusing to rotate it")

    mode = os.environ.get("TORKIT_MODE", "")
    title = os.environ.get("TORKIT_TITLE", "")
    source_path = os.environ.get("TORKIT_SOURCE_PATH", "")

    persistent = data.setdefault("persistent", {})
    general = data.setdefault("general", {})
    persistent["enabled"] = True
    if mode:
        persistent["mode"] = mode
    if title:
        general["title"] = title

    if mode == "share" and source_path:
        data.setdefault("share", {})["filenames"] = [source_path]
    elif mode == "website" and source_path:
        data.setdefault("website", {})["filenames"] = [source_path]
    elif mode == "receive" and source_path:
        data.setdefault("receive", {})["data_dir"] = source_path

    atomic_write(path, json.dumps(data, indent=2) + "\n")


def find_option(arguments: list[str], option: str) -> str | None:
    try:
        index = arguments.index(option)
    except ValueError:
        return None
    if index + 1 >= len(arguments):
        return None
    return arguments[index + 1]


def write_access_file(path: Path, address: str, key: str) -> None:
    atomic_write(
        path,
        f"ONION_ADDRESS={address}\nACCESS_KEY={key}\n",
    )


def main() -> int:
    arguments = sys.argv[1:]
    cli = os.environ.get("TORKIT_CLI", "torkit-cli")
    runtime_dir = Path(os.environ.get("TORKIT_RUNTIME_DIR", "/var/lib/torkit"))
    access_path = Path(
        os.environ.get("TORKIT_ACCESS_FILE", str(runtime_dir / "access.env"))
    )

    pid_path = runtime_dir / "service.pid"
    access_path.unlink(missing_ok=True)
    pid_path.unlink(missing_ok=True)

    persistent_value = find_option(arguments, "--persistent")
    if persistent_value:
        update_persistent_session(Path(persistent_value))

    child = subprocess.Popen(
        [cli, *arguments],
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        bufsize=1,
    )

    atomic_write(pid_path, f"{child.pid}\n")

    def forward_signal(signum: int, _frame: object) -> None:
        if child.poll() is None:
            child.send_signal(signum)

    signal.signal(signal.SIGINT, forward_signal)
    signal.signal(signal.SIGTERM, forward_signal)

    address = ""
    key = ""
    ready_written = False

    try:
        assert child.stdout is not None
        for line in child.stdout:
            onion_match = ONION_RE.search(line)
            if onion_match:
                address = onion_match.group(0)

            key_match = KEY_RE.search(line)
            if key_match:
                key = key_match.group(1)

            if "Give this address" not in line:
                sys.stdout.write(redact_output(line))
                sys.stdout.flush()

            if address and key and not ready_written:
                write_access_file(access_path, address, key)
                print("Onion service published. Access information stored securely.", flush=True)
                ready_written = True

        return child.wait()
    finally:
        pid_path.unlink(missing_ok=True)


if __name__ == "__main__":
    raise SystemExit(main())
