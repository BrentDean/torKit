from __future__ import annotations

import re
import subprocess
import sys
from typing import Sequence

from .model import ROOT, TorKitError

SECRET_RE = re.compile(r"(Private key:)\s*\S+")


def run(
    command: Sequence[str],
    *,
    env: dict[str, str] | None = None,
    check: bool = True,
    capture: bool = True,
) -> subprocess.CompletedProcess[str]:
    result = subprocess.run(
        list(command),
        cwd=ROOT,
        env=env,
        text=True,
        stdout=subprocess.PIPE if capture else None,
        stderr=subprocess.STDOUT if capture else None,
        check=False,
    )
    if check and result.returncode != 0:
        output = SECRET_RE.sub(r"\1 [REDACTED]", result.stdout or "").rstrip()
        if output:
            print(output, file=sys.stderr)
        raise TorKitError(f"command failed: {' '.join(command[:3])}")
    return result
