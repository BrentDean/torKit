from __future__ import annotations

import importlib.util
import os
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

SOURCE = Path(__file__).resolve().parents[1] / "healthcheck.py"
SPEC = importlib.util.spec_from_file_location("torkit_healthcheck", SOURCE)
assert SPEC is not None and SPEC.loader is not None
healthcheck = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(healthcheck)


class HealthcheckTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.state = Path(self.temp.name)

    def write_healthy_state(self) -> None:
        (self.state / "service.pid").write_text(f"{os.getpid()}\n")
        access = self.state / "access.env"
        access.write_text(
            "ONION_ADDRESS=http://" + "a" * 56 + ".onion\n"
            "ACCESS_KEY=" + "A" * 52 + "\n"
        )
        access.chmod(0o600)

    def test_rejects_missing_files(self) -> None:
        self.assertFalse(healthcheck.healthy(self.state))

    def test_accepts_live_child_and_protected_access(self) -> None:
        self.write_healthy_state()
        self.assertTrue(healthcheck.healthy(self.state))

    def test_rejects_dead_child(self) -> None:
        self.write_healthy_state()
        (self.state / "service.pid").write_text("99999999\n")
        self.assertFalse(healthcheck.healthy(self.state))

    def test_rejects_broad_permissions(self) -> None:
        self.write_healthy_state()
        (self.state / "access.env").chmod(0o644)
        self.assertFalse(healthcheck.healthy(self.state))

    def test_rejects_missing_credentials(self) -> None:
        self.write_healthy_state()
        (self.state / "access.env").write_text("ONION_ADDRESS=missing\n")
        self.assertFalse(healthcheck.healthy(self.state))


if __name__ == "__main__":
    unittest.main()
