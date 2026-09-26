from __future__ import annotations

from contextlib import redirect_stdout
from io import StringIO
import json
import os
from pathlib import Path
import sqlite3
import stat
import tempfile
import unittest
from unittest.mock import patch

from torkit_host import config, model, recovery
from torkit_host.model import SERVICES, TorKitError


class RecoveryTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.base = Path(temporary.name)
        self.home = self.base / "home"
        self.home.mkdir()
        self.config = self.home / "config.env"
        self.state = self.home / "state"
        self.state.mkdir()
        self.content = self.base / "content"
        self.content.mkdir()
        self.values = {service.config_key: service.default
                       for service in SERVICES.values()}
        for service in SERVICES.values():
            (self.state / service.name).mkdir()
            if service.uses_directory:
                location = self.content / service.name
                location.mkdir()
                (location / "example.txt").write_text(service.name)
                self.values[service.config_key] = str(location)
        self.config.write_text(
            "".join(f"{key}={value}\n" for key, value in self.values.items())
        )
        self.config.chmod(0o600)
        self.board = self.state / "board"
        (self.board / "session.json").write_text('{"onion_private_key":"secret"}')
        (self.board / "uploads").mkdir()
        (self.board / "uploads" / "picture.png").write_bytes(b"sample image")
        with sqlite3.connect(self.board / "board.sqlite3") as db:
            db.execute("CREATE TABLE posts (body TEXT)")
            db.execute("INSERT INTO posts VALUES ('survives')")
        patchers = [
            patch.object(recovery, "TORKIT_DIR", self.home),
            patch.object(recovery, "CONFIG_FILE", self.config),
            patch.object(recovery, "STATE_ROOT", self.state),
            patch.object(model, "STATE_ROOT", self.state),
            patch.object(config, "TORKIT_DIR", self.home),
            patch.object(config, "CONFIG_FILE", self.config),
            patch.object(config, "STATE_ROOT", self.state),
        ]
        for item in patchers:
            item.start()
            self.addCleanup(item.stop)

    def stage(self, *, include_content=True):
        payload = self.base / "staged" / recovery.SCHEMA
        payload.parent.mkdir(exist_ok=True)
        recovery._stage_payload(payload, self.values, include_content)
        return payload, recovery._validate_payload(payload)

    def test_roundtrip_preserves_board_files_and_onion_identity(self):
        payload, manifest = self.stage()
        self.assertEqual(set(manifest["content"]), {"share", "receive", "website"})
        self.assertIn("state/board/session.json", manifest["files"])
        self.assertIn("state/board/uploads/picture.png", manifest["files"])
        self.config.unlink()
        import shutil
        shutil.rmtree(self.state)
        shutil.rmtree(self.content / "share")
        shutil.rmtree(self.content / "receive")
        shutil.rmtree(self.content / "website")
        recovery._install_payload(payload, manifest, replace=False)
        self.assertEqual(
            (self.state / "board" / "session.json").read_text(),
            '{"onion_private_key":"secret"}',
        )
        with sqlite3.connect(self.state / "board" / "board.sqlite3") as db:
            self.assertEqual(db.execute("SELECT body FROM posts").fetchone(),
                             ("survives",))
        self.assertEqual((self.state / "board" / "uploads" / "picture.png").read_bytes(),
                         b"sample image")
        self.assertEqual(stat.S_IMODE(self.config.stat().st_mode), 0o600)

    def test_snapshot_rejects_symlink(self):
        (self.board / "uploads" / "linked").symlink_to(self.config)
        with self.assertRaisesRegex(TorKitError, "symlink"):
            self.stage()

    def test_snapshot_rejects_corrupt_sqlite(self):
        (self.board / "board.sqlite3").write_text("not a database")
        with self.assertRaisesRegex(TorKitError, "SQLite"):
            self.stage()

    def test_restore_rejects_any_changed_file(self):
        payload, _ = self.stage()
        (payload / "state" / "board" / "session.json").write_text("changed")
        with self.assertRaisesRegex(TorKitError, "SHA-256"):
            recovery._validate_payload(payload)

    def test_nested_manifest_named_file_is_hashed(self):
        (self.content / "receive" / "manifest.json").write_text("keep")
        payload, manifest = self.stage()
        self.assertIn("content/receive/manifest.json", manifest["files"])
        (payload / "content" / "receive" / "manifest.json").write_text("tampered")
        with self.assertRaisesRegex(TorKitError, "SHA-256"):
            recovery._validate_payload(payload)

    def test_restore_requires_replace_for_existing_state(self):
        payload, manifest = self.stage()
        with self.assertRaisesRegex(TorKitError, "--replace"):
            recovery._check_targets(payload, manifest, replace=False)

    def test_replace_removes_previous_content_and_preserves_new_content(self):
        payload, manifest = self.stage()
        (self.state / "board" / "session.json").write_text("older")
        (self.content / "share" / "other.txt").write_text("old")
        recovery._install_payload(payload, manifest, replace=True)
        self.assertEqual((self.state / "board" / "session.json").read_text(),
                         '{"onion_private_key":"secret"}')
        self.assertFalse((self.content / "share" / "other.txt").exists())

    def test_restic_configuration_requires_offhost_sftp_and_private_file(self):
        password = self.base / "password"
        password.write_text("test-only-secret")
        password.chmod(0o600)
        with patch.dict(os.environ, {
            "RESTIC_REPOSITORY": str(self.base / "backup"),
            "RESTIC_PASSWORD_FILE": str(password),
        }):
            with self.assertRaisesRegex(TorKitError, "off-host"):
                recovery._settings()
        password.chmod(0o644)
        with patch.dict(os.environ, {
            "RESTIC_REPOSITORY": "sftp:backup-host:/torkit",
            "RESTIC_PASSWORD_FILE": str(password),
        }):
            with self.assertRaisesRegex(TorKitError, "0600"):
                recovery._settings()

    def test_lock_refuses_duplicate_backups(self):
        with recovery._exclusive():
            with self.assertRaisesRegex(TorKitError, "another"):
                with recovery._exclusive():
                    pass

    def test_startup_check_reports_missing_linger(self):
        from subprocess import CompletedProcess
        with patch.object(recovery.shutil, "which", return_value="/usr/bin/command"):
            with patch.object(recovery.subprocess, "run", side_effect=[
                CompletedProcess([], 0, "no", ""),
                CompletedProcess([], 0, "enabled", ""),
                CompletedProcess([], 0, "active", ""),
            ]):
                with redirect_stdout(StringIO()) as output:
                    with self.assertRaisesRegex(TorKitError, "incomplete"):
                        recovery.startup_check()
        self.assertIn("enable-linger", output.getvalue())

    def test_backup_resumes_services_before_remote_transfer(self):
        fake = type("Fake", (), {})()
        from unittest.mock import Mock
        fake.require = Mock()
        fake.status = Mock(side_effect=lambda s: "running" if s.name == "board" else "absent")
        fake.configuration_changed = Mock(return_value=False)
        fake.stop_many = Mock()
        fake.ensure = Mock()

        def upload(command):
            self.assertEqual(command[0], "backup")
            fake.ensure.assert_called_once_with([SERVICES["board"]])
            return json.dumps({"message_type": "summary", "snapshot_id": "a" * 64})

        with patch.object(recovery, "_settings"):
            with patch.object(recovery, "TorKitRuntime", return_value=fake):
                with patch.object(recovery, "load_config", return_value=self.values):
                    with patch.object(recovery, "_restic", side_effect=upload):
                        with redirect_stdout(StringIO()) as output:
                            recovery.backup(include_content=False)
        fake.stop_many.assert_called_once_with([SERVICES["board"]])
        self.assertIn("Snapshot:", output.getvalue())

    def test_failed_staging_still_resumes_active_service(self):
        from unittest.mock import Mock
        fake = Mock()
        fake.status.side_effect = lambda s: "running" if s.name == "board" else "absent"
        fake.configuration_changed.return_value = False
        with patch.object(recovery, "_settings"):
            with patch.object(recovery, "TorKitRuntime", return_value=fake):
                with patch.object(recovery, "load_config", return_value=self.values):
                    with patch.object(recovery, "_stage_payload",
                                      side_effect=TorKitError("staging failed")):
                        with self.assertRaisesRegex(TorKitError, "staging failed"):
                            with redirect_stdout(StringIO()):
                                recovery.backup(include_content=False)
        fake.ensure.assert_called_once_with([SERVICES["board"]])

    def test_restore_refuses_running_services(self):
        from unittest.mock import Mock
        fake = Mock()
        fake.status.side_effect = lambda s: "running" if s.name == "board" else "absent"
        with patch.object(recovery, "_settings"):
            with patch.object(recovery, "TorKitRuntime", return_value=fake):
                with self.assertRaisesRegex(TorKitError, "stop all"):
                    recovery.restore("latest")

    def test_backup_rejects_loopback_repository(self):
        password = self.base / "password"
        password.write_text("test-only-secret")
        password.chmod(0o600)
        with patch.dict(os.environ, {
            "RESTIC_REPOSITORY": "sftp:localhost:/tmp/not-off-host",
            "RESTIC_PASSWORD_FILE": str(password),
        }):
            with self.assertRaisesRegex(TorKitError, "cannot be"):
                recovery._settings()

    def test_startup_check_requires_rootless_daemon(self):
        from subprocess import CompletedProcess
        from unittest.mock import Mock
        docker = Mock()
        docker.rootless = False
        with patch.object(recovery.shutil, "which", return_value="/usr/bin/command"):
            with patch.object(recovery.subprocess, "run", side_effect=[
                CompletedProcess([], 0, "yes", ""),
                CompletedProcess([], 0, "enabled", ""),
                CompletedProcess([], 0, "active", ""),
            ]):
                with patch.object(recovery, "DockerClient", return_value=docker):
                    with redirect_stdout(StringIO()):
                        with self.assertRaisesRegex(TorKitError, "not rootless"):
                            recovery.startup_check()

    def test_restore_command_installs_snapshot_only_after_confirmation(self):
        from unittest.mock import Mock
        import shutil

        source, _ = self.stage()
        fake = Mock()
        fake.status.return_value = "absent"

        def fake_restic(command):
            self.assertEqual(command[0], "restore")
            restored = Path(command[command.index("--target") + 1])
            destination = restored / "home" / "torkit" / recovery.SCHEMA
            destination.parent.mkdir(parents=True)
            shutil.copytree(source, destination)
            return ""

        (self.state / "board" / "session.json").write_text("not restored yet")
        with patch.object(recovery, "_settings"):
            with patch.object(recovery, "TorKitRuntime", return_value=fake):
                with patch.object(recovery, "_restic", side_effect=fake_restic):
                    with patch.object(recovery.sys.stdin, "isatty", return_value=True):
                        with patch("builtins.input", return_value="RESTORE"):
                            with redirect_stdout(StringIO()):
                                recovery.restore("latest", replace=True)
        self.assertEqual(
            (self.state / "board" / "session.json").read_text(),
            '{"onion_private_key":"secret"}',
        )
        fake.docker.compose.assert_not_called()

    def test_restore_cancel_preserves_existing_data(self):
        from unittest.mock import Mock
        import shutil

        source, _ = self.stage()
        fake = Mock()
        fake.status.return_value = "absent"

        def fake_restic(command):
            restored = Path(command[command.index("--target") + 1])
            destination = restored / "home" / "torkit" / recovery.SCHEMA
            destination.parent.mkdir(parents=True)
            shutil.copytree(source, destination)
            return ""

        (self.state / "board" / "session.json").write_text("original")
        with patch.object(recovery, "_settings"):
            with patch.object(recovery, "TorKitRuntime", return_value=fake):
                with patch.object(recovery, "_restic", side_effect=fake_restic):
                    with patch.object(recovery.sys.stdin, "isatty", return_value=True):
                        with patch("builtins.input", return_value="NO"):
                            with self.assertRaisesRegex(TorKitError, "cancelled"):
                                with redirect_stdout(StringIO()):
                                    recovery.restore("latest", replace=True)
        self.assertEqual((self.state / "board" / "session.json").read_text(),
                         "original")

    def test_failed_install_rolls_back_previous_config_and_state(self):
        from unittest.mock import patch
        import os

        payload, manifest = self.stage()
        self.config.write_text(self.config.read_text() + "# preserved original\n")
        (self.state / "board" / "session.json").write_text("original identity")
        original_replace = os.replace

        def fail_on_state_install(source, destination):
            source = Path(source)
            if (destination == self.state and
                    source.name.startswith(".state.torkit-new-")):
                raise OSError("simulated failed state install")
            return original_replace(source, destination)

        with patch.object(recovery.os, "replace", side_effect=fail_on_state_install):
            with self.assertRaisesRegex(OSError, "simulated"):
                recovery._install_payload(payload, manifest, replace=True)
        self.assertEqual((self.state / "board" / "session.json").read_text(),
                         "original identity")
        self.assertIn("# preserved original", self.config.read_text())

    def test_restore_does_not_require_existing_config_on_new_host(self):
        from unittest.mock import Mock

        self.config.unlink()
        fake = Mock()
        fake.status.side_effect = lambda s: "running" if s.name == "board" else "absent"

        def check_config(values):
            self.assertEqual(set(values), {s.config_key for s in SERVICES.values()})
            return fake

        with patch.object(recovery, "_settings"):
            with patch.object(recovery, "TorKitRuntime", side_effect=check_config):
                with self.assertRaisesRegex(TorKitError, "stop all"):
                    recovery.restore("latest")
        self.assertFalse(self.config.exists())

    def test_backup_refuses_configured_content_overlapping_runtime(self):
        self.values["SHARE_DIR"] = str(self.home)
        with self.assertRaisesRegex(TorKitError, "overlaps TorKit state"):
            self.stage()

    def test_s3_requires_https_amazon_endpoint_and_credentials(self):
        password = self.base / "password"
        password.write_text("test-only-secret")
        password.chmod(0o600)
        with patch.dict(os.environ, {
            "RESTIC_REPOSITORY": "s3:s3.us-east-1.amazonaws.com/example-backups-123/torkit",
            "RESTIC_PASSWORD_FILE": str(password),
            "AWS_ACCESS_KEY_ID": "test-access-id",
            "AWS_SECRET_ACCESS_KEY": "test-access-secret",
        }):
            with patch.object(recovery.shutil, "which", return_value="/usr/bin/restic"):
                arguments, environment = recovery._settings()
        self.assertEqual(arguments[1:3], [
            "--repo", "s3:s3.us-east-1.amazonaws.com/example-backups-123/torkit",
        ])
        self.assertEqual(environment["AWS_ACCESS_KEY_ID"], "test-access-id")

    def test_s3_refuses_missing_keys_and_non_amazon_endpoint(self):
        password = self.base / "password"
        password.write_text("test-only-secret")
        password.chmod(0o600)
        with patch.dict(os.environ, {
            "RESTIC_REPOSITORY": "s3:s3.us-east-1.amazonaws.com/example-backups-123/torkit",
            "RESTIC_PASSWORD_FILE": str(password),
            "AWS_ACCESS_KEY_ID": "",
            "AWS_SECRET_ACCESS_KEY": "",
        }):
            with self.assertRaisesRegex(TorKitError, "AWS_ACCESS_KEY_ID"):
                recovery._settings()
        with patch.dict(os.environ, {
            "RESTIC_REPOSITORY": "s3:http://localhost:9000/example-backups-123/torkit",
            "RESTIC_PASSWORD_FILE": str(password),
        }):
            with self.assertRaisesRegex(TorKitError, "REGION"):
                recovery._settings()


if __name__ == "__main__":
    unittest.main()
