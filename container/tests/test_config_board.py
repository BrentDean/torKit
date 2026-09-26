from __future__ import annotations

import stat
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from torkit_host import config
from torkit_host.model import TorKitError


class BoardConfigUpgradeTests(unittest.TestCase):
    def test_existing_config_gains_board_without_changing_other_values(self):
        with tempfile.TemporaryDirectory() as folder:
            directory = Path(folder)
            path = directory / "config.env"
            original = (
                "CHAT_NAME=Existing private chat\n"
                "SHARE_DIR=/tmp/my-share\n"
                "RECEIVE_DIR=/tmp/my-receive\n"
                "WEBSITE_DIR=/tmp/my-site\n"
            )
            path.write_text(original, encoding="utf-8")
            with patch.object(config, "TORKIT_DIR", directory):
                with patch.object(config, "CONFIG_FILE", path):
                    loaded = config.load_config()
                    again = config.load_config()
            self.assertEqual(loaded["CHAT_NAME"], "Existing private chat")
            self.assertEqual(loaded["SHARE_DIR"], "/tmp/my-share")
            self.assertEqual(loaded["BOARD_NAME"], "Private board")
            self.assertEqual(loaded, again)
            self.assertEqual(
                path.read_text(encoding="utf-8"),
                original + "BOARD_NAME=Private board\n",
            )
            self.assertEqual(stat.S_IMODE(path.stat().st_mode), 0o600)

    def test_missing_existing_setting_still_fails_closed(self):
        with tempfile.TemporaryDirectory() as folder:
            directory = Path(folder)
            path = directory / "config.env"
            path.write_text("CHAT_NAME=Only chat\n", encoding="utf-8")
            with patch.object(config, "TORKIT_DIR", directory):
                with patch.object(config, "CONFIG_FILE", path):
                    with self.assertRaises(TorKitError):
                        config.load_config()


if __name__ == "__main__":
    unittest.main()
