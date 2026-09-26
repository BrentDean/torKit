from __future__ import annotations

import importlib.util
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

SOURCE = Path(__file__).resolve().parents[2] / "cli/onionshare_cli/mode_settings.py"
SPEC = importlib.util.spec_from_file_location("torkit_mode_settings", SOURCE)
assert SPEC is not None and SPEC.loader is not None
mode_settings_module = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(mode_settings_module)


class FakeCommon:
    platform = "Linux"

    def __init__(self, directory: Path) -> None:
        self.directory = directory
        self.messages: list[str] = []

    def build_password(self, _words: int) -> str:
        return "test-session"

    def build_persistent_dir(self) -> str:
        return str(self.directory)

    def log(self, _module: str, _method: str, message: str) -> None:
        self.messages.append(message)


class ModeSettingsRedactionTests(unittest.TestCase):
    def test_sensitive_settings_are_retained_but_not_logged(self) -> None:
        with TemporaryDirectory() as directory:
            common = FakeCommon(Path(directory))
            settings = mode_settings_module.ModeSettings(
                common, filename=str(Path(directory) / "session.json"),
                id="test-session",
            )
            for group, key in (
                ("onion", "private_key"),
                ("onion", "client_auth_priv_key"),
                ("onion", "client_auth_pub_key"),
                ("general", "service_id"),
            ):
                secret = f"unique-secret-{key}"
                settings.set(group, key, secret)
                self.assertEqual(settings.get(group, key), secret)
                self.assertNotIn(secret, "\n".join(common.messages))
            self.assertIn("[REDACTED]", "\n".join(common.messages))


if __name__ == "__main__":
    unittest.main()
