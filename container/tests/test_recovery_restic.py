"""Offline integration: real restic repository, no SFTP or production credentials."""

from __future__ import annotations

import json
import os
from pathlib import Path
import shutil
import sqlite3
import subprocess
import tempfile
import unittest
from unittest.mock import patch

from torkit_host import model, recovery
from torkit_host.model import SERVICES


@unittest.skipUnless(shutil.which("restic"), "install restic to run integration")
class ResticRoundTripTests(unittest.TestCase):
    def test_real_restic_backup_restore_path_and_content(self):
        with tempfile.TemporaryDirectory() as temporary:
            base = Path(temporary)
            home = base / "home"
            home.mkdir()
            config = home / "config.env"
            state = home / "state"
            state.mkdir()
            for service in SERVICES.values():
                (state / service.name).mkdir()
            board = state / "board"
            (board / "session.json").write_text('{"private_key":"test-only"}')
            with sqlite3.connect(board / "board.sqlite3") as db:
                db.execute("CREATE TABLE posts (body TEXT)")
                db.execute("INSERT INTO posts VALUES ('restored')")
            values = {
                service.config_key: service.default
                for service in SERVICES.values()
            }
            for service in SERVICES.values():
                if service.uses_directory:
                    directory = base / service.name
                    directory.mkdir()
                    (directory / "content.txt").write_text(service.name)
                    values[service.config_key] = str(directory)
            config.write_text(
                "".join(f"{key}={value}\n" for key, value in values.items())
            )
            config.chmod(0o600)

            password = base / "password"
            password.write_text("integration-test-only-password")
            password.chmod(0o600)
            repository = base / "repository"
            command = ["restic", "-r", str(repository), "--password-file",
                       str(password)]
            env = os.environ.copy()
            env.pop("RESTIC_PASSWORD", None)

            def restic(*args):
                result = subprocess.run([*command, *args], env=env,
                                        text=True, capture_output=True)
                self.assertEqual(result.returncode, 0, result.stderr)
                return result.stdout

            with patch.object(recovery, "CONFIG_FILE", config):
                with patch.object(recovery, "STATE_ROOT", state):
                    with patch.object(model, "STATE_ROOT", state):
                        payload = home / ".recovery-test" / recovery.SCHEMA
                        payload.parent.mkdir()
                        recovery._stage_payload(payload, values)
                        original = recovery._validate_payload(payload)
                        restic("init")
                        result = restic("backup", "--json", "--tag",
                                        recovery.SCHEMA, str(payload))
                        summary = [
                            json.loads(line) for line in result.splitlines()
                            if '"message_type":"summary"' in line
                        ]
                        self.assertTrue(summary)
                        self.assertTrue(summary[-1]["snapshot_id"])
                        target = base / "restored"
                        restic("restore", summary[-1]["snapshot_id"],
                               "--target", str(target))
                        matches = [
                            item.parent for item in target.rglob("manifest.json")
                            if item.parent.name == recovery.SCHEMA
                        ]
                        self.assertEqual(len(matches), 1)
                        restored = recovery._validate_payload(matches[0])
                        self.assertEqual(original["files"], restored["files"])


if __name__ == "__main__":
    unittest.main()
