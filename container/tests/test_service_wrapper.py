from __future__ import annotations

import importlib.util
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

SOURCE = Path(__file__).resolve().parents[1] / "run-service.py"
SPEC = importlib.util.spec_from_file_location("torkit_service_wrapper", SOURCE)
assert SPEC is not None and SPEC.loader is not None
wrapper = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(wrapper)


class ServiceWrapperTests(unittest.TestCase):
    def test_redacts_private_keys_and_onion_addresses(self) -> None:
        secret = "A" * 52
        line = (
            "ModeSettings.set: onion.client_auth_priv_key = " + secret + "\n"
            "Private key: " + secret + "\n"
            "Onion address: http://" + "a" * 56 + ".onion\n"
        )
        clean = wrapper.redact_output(line)
        self.assertNotIn(secret, clean)
        self.assertNotIn("a" * 56, clean)
        self.assertIn("[REDACTED]", clean)

    def test_corrupt_persistent_state_fails_closed(self) -> None:
        with TemporaryDirectory() as directory:
            path = Path(directory) / "session.json"
            path.write_text("{bad json")
            with self.assertRaisesRegex(RuntimeError, "refusing to rotate"):
                wrapper.update_persistent_session(path)

    def test_missing_persistent_identity_fails_closed(self) -> None:
        with TemporaryDirectory() as directory:
            path = Path(directory) / "session.json"
            path.write_text(json.dumps({"onion": {}, "persistent": {}}))
            with self.assertRaisesRegex(RuntimeError, "no onion identity"):
                wrapper.update_persistent_session(path)

    def test_refresh_keeps_identity(self) -> None:
        with TemporaryDirectory() as directory:
            path = Path(directory) / "session.json"
            path.write_text(json.dumps({
                "onion": {"private_key": "keep-me"},
                "persistent": {"enabled": True},
                "general": {"title": "Old"},
            }))
            wrapper.update_persistent_session(path)
            self.assertEqual(
                json.loads(path.read_text())["onion"]["private_key"], "keep-me"
            )
            self.assertEqual(path.stat().st_mode & 0o777, 0o600)


if __name__ == "__main__":
    unittest.main()
