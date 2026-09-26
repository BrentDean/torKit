#!/usr/bin/env python3
"""Privacy preflight: source and reachable Git-history patterns (no matched values logged).

This is a *heuristic* check, not a guarantee that all private information is absent.
Review screenshots, archives, Git history and legal publication permissions separately.
"""
from __future__ import annotations

import collections
import pathlib
import re
import subprocess
import sys

ROOT = pathlib.Path(__file__).resolve().parents[1]
# Avoid embedding names, ZIP codes, addresses, or other actual personal identifiers
# into the proposed public source code. Inspect matches privately after a failed run.
PATTERNS = {
    "workstation-home-directory": re.compile(
        rb"/home/(?!USERNAME/|USER/|example/|username/)[A-Za-z0-9_.-]+/"
        rb"(?:Desktop|Documents|Downloads|Tools|Projects|projects)/", re.I
    ),
    "macos-personal-home-directory": re.compile(
        rb"/Users/(?!USERNAME/|USER/|example/|username/)[A-Za-z0-9_.-]+/"
        rb"(?:Desktop|Documents|Downloads|Tools|Projects|projects)/", re.I
    ),
    "real-postal-code-configuration": re.compile(
        rb"""(?:zip_code|postal_code|postcode)["']?\s*[:=]\s*["']?\d{5}\b""", re.I
    ),
    "private-key-block": re.compile(
        rb"-----BEGIN (?:OPENSSH|RSA|EC|DSA|ENCRYPTED|PRIVATE) PRIVATE KEY-----"
    ),
}
MAX_FILE_BYTES = 30 * 1024 * 1024


def scan_current() -> dict[str, set[str]]:
    results: dict[str, set[str]] = collections.defaultdict(set)
    paths = subprocess.check_output(["git", "ls-files", "-z"], cwd=ROOT).split(b"\0")
    for raw in paths:
        if not raw:
            continue
        path = raw.decode("utf-8", "replace")
        candidate = ROOT / path
        if not candidate.is_file() or candidate.stat().st_size > MAX_FILE_BYTES:
            continue
        try:
            data = candidate.read_bytes()
        except OSError:
            continue
        for category, regex in PATTERNS.items():
            if regex.search(data):
                results[category].add(path)
    return results


def scan_history() -> dict[str, set[str]]:
    results: dict[str, set[str]] = collections.defaultdict(set)
    cmd = ["git", "log", "--all", "--no-ext-diff", "--no-textconv",
           "--format=commit:%H", "-p", "--"]
    with subprocess.Popen(cmd, cwd=ROOT, stdout=subprocess.PIPE,
                          stderr=subprocess.DEVNULL) as process:
        if process.stdout is None:
            raise RuntimeError("Git log stream unavailable")
        path = "[unknown]"
        commit = "[unknown]"
        for line in process.stdout:
            if line.startswith(b"commit:"):
                commit = line[7:].decode("ascii", "replace").strip()[:12]
            elif line.startswith(b"+++ b/"):
                path = line[6:].decode("utf-8", "replace").strip()
            elif line.startswith(b"+") and not line.startswith(b"+++"):
                for category, regex in PATTERNS.items():
                    if regex.search(line):
                        results[category].add(f"{path} @ {commit}")
        code = process.wait()
        if code:
            raise RuntimeError(f"Git history enumeration failed ({code})")
    return results


def main() -> int:
    current = scan_current()
    history = scan_history()
    print("Privacy heuristic results (categories, file paths, abbreviated commits only):")
    for label, findings in [("current tree", current), ("reachable history", history)]:
        count = sum(map(len, findings.values()))
        print(f"{label}: {count} finding(s)")
        for category, entries in sorted(findings.items()):
            print(f"  {category}: {len(entries)}")
            for entry in sorted(entries)[:8]:
                print(f"    {entry}")
            if len(entries) > 8:
                print(f"    ... and {len(entries) - 8} more")
    if current or history:
        print("REVIEW REQUIRED: inspect findings privately, including older commits.")
        return 1
    print("No matches for these limited heuristics. This does not certify publication safety.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
