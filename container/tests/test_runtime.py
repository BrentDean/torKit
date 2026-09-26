from __future__ import annotations

from contextlib import redirect_stdout
from io import StringIO
from pathlib import Path
import tempfile
from unittest.mock import call, patch
import unittest

from torkit_host.docker import ContainerInfo, DockerClient
from torkit_host.model import SERVICES
from torkit_host.runtime import TorKitRuntime


class RuntimeTests(unittest.TestCase):
    def make_runtime(self) -> TorKitRuntime:
        config = {
            service.config_key: service.default
            for service in SERVICES.values()
        }
        with patch.object(DockerClient, "_fingerprint", return_value="build-123"):
            return TorKitRuntime(config)

    def test_environment_is_driven_by_service_registry(self) -> None:
        runtime = self.make_runtime()
        environment = runtime._environment("build-123")

        self.assertEqual(environment["TORKIT_BUILD_ID"], "build-123")
        for service in SERVICES.values():
            expected_value = (
                runtime.host_path(service)
                if service.uses_directory
                else runtime.value(service)
            )
            self.assertEqual(environment[service.value_env], expected_value)
            self.assertEqual(environment[service.state_env], str(service.state_dir))

    def test_rootless_environment_maps_to_host_daemon_owner(self) -> None:
        runtime = self.make_runtime()
        with patch("torkit_host.runtime.os.getuid", return_value=1001):
            with patch("torkit_host.runtime.os.getgid", return_value=1001):
                runtime.docker.rootless = False
                regular = runtime._environment("build-123")
                runtime.docker.rootless = True
                rootless = runtime._environment("build-123")
        self.assertEqual(regular["TORKIT_CONTAINER_UID"], "1001")
        self.assertEqual(regular["TORKIT_CONTAINER_GID"], "1001")
        self.assertEqual(rootless["TORKIT_CONTAINER_UID"], "0")
        self.assertEqual(rootless["TORKIT_CONTAINER_GID"], "0")

    def test_recreate_services_uses_one_common_pipeline(self) -> None:
        runtime = self.make_runtime()
        services = [SERVICES["chat"], SERVICES["share"]]

        with patch.object(runtime, "prepare_directory") as prepare_mock:
            with patch.object(runtime.docker, "ensure_image") as image_mock:
                with patch.object(runtime, "_remove_access_files") as access_mock:
                    with patch.object(runtime.docker, "compose") as compose_mock:
                        runtime._recreate_services(services)

        self.assertEqual(
            prepare_mock.call_args_list,
            [call(SERVICES["chat"]), call(SERVICES["share"])],
        )
        image_mock.assert_called_once_with()
        access_mock.assert_called_once_with(services)
        compose_mock.assert_called_once_with(
            "up",
            "--detach",
            "--no-build",
            "--force-recreate",
            "chat",
            "fileshare",
        )

    def test_ready_uses_one_container_snapshot(self) -> None:
        runtime = self.make_runtime()
        info = ContainerInfo(
            name="torkit-chat-1",
            state="running",
            health="healthy",
            restart_count="0",
            started_at="2026-08-03T18:00:00Z",
            image="torkit:local",
        )

        with patch.object(runtime.docker, "container_info", return_value=info) as info_mock:
            with patch.object(
                runtime,
                "access",
                return_value={
                    "ONION_ADDRESS": "http://example.onion",
                    "ACCESS_KEY": "private-key",
                },
            ):
                self.assertTrue(runtime.ready(SERVICES["chat"]))

        info_mock.assert_called_once_with(SERVICES["chat"])

    def test_ensure_is_idempotent_for_ready_services(self) -> None:
        runtime = self.make_runtime()
        services = [SERVICES["chat"], SERVICES["website"]]
        output = StringIO()
        with patch.object(runtime.docker, "image_is_current", return_value=True):
            with patch.object(runtime, "ready", return_value=True):
                with patch.object(runtime, "configuration_changed", return_value=False):
                    with patch.object(runtime, "_recreate_services") as recreate:
                        with redirect_stdout(output):
                            runtime.ensure(services)
        recreate.assert_not_called()
        self.assertIn("Chat: already ready", output.getvalue())
        self.assertIn("Website: already ready", output.getvalue())

    def test_wait_ready_reports_health_transitions_without_credentials(self) -> None:
        runtime = self.make_runtime()
        output = StringIO()
        with patch.object(runtime, "ready", side_effect=[False, False, True]):
            with patch.object(runtime, "status", return_value="running"):
                with patch.object(
                    runtime.docker, "health", side_effect=["starting", "healthy"]
                ):
                    with patch("torkit_host.runtime.time.sleep"):
                        with redirect_stdout(output):
                            runtime.wait_ready(SERVICES["chat"])

        text = output.getvalue()
        self.assertIn("Chat: container=running, health=starting", text)
        self.assertIn("Chat: container=running, health=healthy", text)
        self.assertNotIn("ONION_ADDRESS", text)
        self.assertNotIn("ACCESS_KEY", text)

    def test_ensure_recreates_unhealthy_service_without_printing_keys(self) -> None:
        runtime = self.make_runtime()
        services = [SERVICES["chat"], SERVICES["website"]]
        output = StringIO()
        with patch.object(runtime.docker, "image_is_current", return_value=True):
            with patch.object(
                runtime, "ready",
                side_effect=lambda service: service.name == "website",
            ):
                with patch.object(runtime, "configuration_changed", return_value=False):
                    with patch.object(runtime, "_recreate_services") as recreate:
                        with patch.object(runtime, "wait_ready") as wait:
                            with redirect_stdout(output):
                                runtime.ensure(services)
        recreate.assert_called_once_with([SERVICES["chat"]])
        wait.assert_called_once_with(SERVICES["chat"])
        self.assertNotIn("Access key", output.getvalue())
        self.assertIn("Chat: reconciling container and image", output.getvalue())
        self.assertIn("Chat: waiting for Docker health and Tor publication", output.getvalue())
        self.assertIn("Ready: Chat (healthy, onion access recorded;", output.getvalue())
        self.assertIn("Website: already ready", output.getvalue())

    def test_ensure_recreates_selected_services_on_image_change(self) -> None:
        runtime = self.make_runtime()
        services = [SERVICES["chat"], SERVICES["website"]]
        with patch.object(runtime.docker, "image_is_current", return_value=False):
            with patch.object(runtime, "_recreate_services") as recreate:
                with patch.object(runtime, "wait_ready"):
                    with redirect_stdout(StringIO()):
                        runtime.ensure(services)
        recreate.assert_called_once_with(services)

    def test_health_snapshot_never_contains_credentials(self) -> None:
        runtime = self.make_runtime()
        info = ContainerInfo(
            name="torkit-chat-1",
            state="running",
            health="healthy",
            restart_count="2",
            started_at="2026-08-03T18:00:00Z",
            image="torkit:local",
        )
        with patch.object(
            runtime.docker, "container_info",
            side_effect=lambda service: info if service.name == "chat" else None,
        ):
            with patch.object(runtime, "ready", return_value=True):
                snapshot = runtime.health_snapshot()
        self.assertTrue(snapshot["chat"]["ready"])
        self.assertEqual(snapshot["share"]["state"], "absent")
        self.assertNotIn("ACCESS_KEY", str(snapshot))
        self.assertNotIn("ONION_ADDRESS", str(snapshot))

    def test_burn_removes_only_selected_container_and_state(self) -> None:
        runtime = self.make_runtime()
        selected = SERVICES["chat"]
        with patch.object(runtime, "status", return_value="running"):
            with patch.object(runtime.docker, "compose") as compose:
                with patch.object(runtime, "_clear_service_state") as clear:
                    with redirect_stdout(StringIO()):
                        runtime.burn([selected])
        compose.assert_called_once_with("rm", "--force", "--stop", "chat")
        clear.assert_called_once_with(selected)

    def test_burn_board_deletes_its_posts_without_touching_chat(self) -> None:
        runtime = self.make_runtime()
        with tempfile.TemporaryDirectory() as temporary:
            with patch("torkit_host.model.STATE_ROOT", Path(temporary)):
                board = SERVICES["board"]
                chat = SERVICES["chat"]
                board.state_dir.mkdir()
                chat.state_dir.mkdir()
                (board.state_dir / "board.sqlite3").write_text("test-board-data")
                (board.state_dir / "session.json").write_text("test-board-identity")
                (chat.state_dir / "session.json").write_text("keep-chat-identity")
                with patch.object(runtime, "status", return_value="absent"):
                    with patch.object(runtime.docker, "compose") as compose:
                        with redirect_stdout(StringIO()):
                            runtime.burn([board])
                compose.assert_not_called()
                self.assertFalse((board.state_dir / "board.sqlite3").exists())
                self.assertFalse((board.state_dir / "session.json").exists())
                self.assertTrue((chat.state_dir / "session.json").exists())

    def test_clear_board_keeps_identity_and_chat_when_active(self) -> None:
        runtime = self.make_runtime()
        with tempfile.TemporaryDirectory() as directory:
            with patch("torkit_host.model.STATE_ROOT", Path(directory)):
                board, chat = SERVICES["board"], SERVICES["chat"]
                board.state_dir.mkdir()
                chat.state_dir.mkdir()
                (board.state_dir / "session.json").write_text("board identity")
                (board.state_dir / "board.sqlite3").write_bytes(b"posts")
                (board.state_dir / "board.sqlite3-journal").write_bytes(b"journal")
                uploads = board.state_dir / "uploads"
                uploads.mkdir()
                (uploads / "sample.png").write_bytes(b"uploaded-image")
                (chat.state_dir / "session.json").write_text("chat identity")
                with patch.object(runtime, "status", return_value="running"):
                    with patch.object(runtime, "stop_many") as stop:
                        with patch.object(runtime, "ensure") as ensure:
                            with redirect_stdout(StringIO()):
                                self.assertTrue(runtime.clear_board())
                stop.assert_called_once_with([board])
                ensure.assert_called_once_with([board])
                self.assertFalse((board.state_dir / "board.sqlite3").exists())
                self.assertFalse((board.state_dir / "board.sqlite3-journal").exists())
                self.assertFalse((board.state_dir / "uploads").exists())
                self.assertTrue((board.state_dir / "session.json").is_file())
                self.assertTrue((chat.state_dir / "session.json").is_file())

    def test_clear_board_offline_does_not_restart(self) -> None:
        runtime = self.make_runtime()
        with tempfile.TemporaryDirectory() as directory:
            with patch("torkit_host.model.STATE_ROOT", Path(directory)):
                board = SERVICES["board"]
                board.state_dir.mkdir()
                (board.state_dir / "session.json").write_text("board identity")
                (board.state_dir / "board.sqlite3").write_bytes(b"posts")
                with patch.object(runtime, "status", return_value="exited"):
                    with patch.object(runtime, "stop_many") as stop:
                        with patch.object(runtime, "ensure") as ensure:
                            with redirect_stdout(StringIO()):
                                self.assertFalse(runtime.clear_board())
                stop.assert_not_called()
                ensure.assert_not_called()
                self.assertTrue((board.state_dir / "session.json").is_file())
                self.assertFalse((board.state_dir / "board.sqlite3").exists())

    def test_burn_absent_service_only_clears_state(self) -> None:
        runtime = self.make_runtime()
        selected = SERVICES["chat"]
        with patch.object(runtime, "status", return_value="absent"):
            with patch.object(runtime.docker, "compose") as compose:
                with patch.object(runtime, "_clear_service_state") as clear:
                    with redirect_stdout(StringIO()):
                        runtime.burn([selected])
        compose.assert_not_called()
        clear.assert_called_once_with(selected)

    def test_reset_reads_each_selected_status_once(self) -> None:
        runtime = self.make_runtime()
        services = [SERVICES["chat"], SERVICES["share"]]
        statuses = {"chat": "running", "share": "exited"}

        with patch.object(
            runtime,
            "status",
            side_effect=lambda service: statuses[service.name],
        ) as status_mock:
            with patch.object(runtime.docker, "compose") as compose_mock:
                with patch.object(runtime, "_clear_service_state"):
                    with patch.object(runtime, "start") as start_mock:
                        with redirect_stdout(StringIO()):
                            runtime.reset_identities(services)

        self.assertEqual(status_mock.call_count, len(services))
        compose_mock.assert_called_once_with(
            "rm",
            "--force",
            "--stop",
            "chat",
            "fileshare",
        )
        start_mock.assert_called_once_with([SERVICES["chat"]])


if __name__ == "__main__":
    unittest.main()
