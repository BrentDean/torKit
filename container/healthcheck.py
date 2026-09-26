#!/usr/bin/env python3
"""Local container readiness probe. Never emit onion addresses or credentials."""

from __future__ import annotations

import os
from pathlib import Path
import re

ONION_RE = re.compile(r"^http://[a-z2-7]{56}\.onion$")
KEY_RE = re.compile(r"^[A-Z2-7]{52}$")


def healthy(state_dir: Path) -> bool:
    try:
        pid = int((state_dir / "service.pid").read_text(encoding="ascii").strip())
        if pid <= 1:
            return False

        os.kill(pid, 0)
        proc_stat = Path(f"/proc/{pid}/stat")
        if proc_stat.exists():
            # /proc/PID/stat: the state character follows the final ')' of comm.
            process_state = proc_stat.read_text(encoding="ascii").rsplit(") ", 1)[1][0]
            if process_state in {"Z", "X", "x"}:
                return False

        access_file = state_dir / "access.env"
        if access_file.stat().st_mode & 0o077:
            return False
        values = dict(
            line.split("=", 1)
            for line in access_file.read_text(encoding="ascii").splitlines()
            if "=" in line
        )
        return bool(
            ONION_RE.fullmatch(values.get("ONION_ADDRESS", ""))
            and KEY_RE.fullmatch(values.get("ACCESS_KEY", ""))
        )
    except (OSError, ValueError, IndexError, UnicodeError):
        return False


if __name__ == "__main__":
    raise SystemExit(0 if healthy(Path(os.environ.get(
        "TORKIT_RUNTIME_DIR", "/var/lib/torkit"
    ))) else 1)
