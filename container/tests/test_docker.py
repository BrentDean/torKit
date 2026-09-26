from __future__ import annotations

import subprocess
import unittest
from unittest.mock import patch

from torkit_host.docker import ContainerInfo, DockerClient
from torkit_host.model import COMPOSE_FILE, PROJECT_NAME, ROOT, SERVICES


class DockerClientTests(unittest.TestCase):
    def make_client(self) -> DockerClient:
        with patch.object(DockerClient, "_fingerprint", return_value="build-123"):
            return DockerClient(lambda build_id: {"TORKIT_BUILD_ID": build_id})

    def test_compose_uses_registry_profiles_and_environment(self) -> None:
        client = self.make_client()
        result = subprocess.CompletedProcess(args=[], returncode=0, stdout="")

        with patch("torkit_host.docker.run", return_value=result) as run_mock:
            actual = client.compose("ps", "--all")

        self.assertIs(actual, result)
        command = run_mock.call_args.args[0]
        self.assertEqual(command[:2], ["docker", "compose"])
        self.assertIn(str(ROOT), command)
        self.assertIn(PROJECT_NAME, command)
        self.assertIn(str(COMPOSE_FILE), command)

        profiles = [
            command[index + 1]
            for index, token in enumerate(command[:-1])
            if token == "--profile"
        ]
        self.assertEqual(
            profiles,
            [service.compose_name for service in SERVICES.values()],
        )
        self.assertEqual(command[-2:], ["ps", "--all"])
        self.assertEqual(
            run_mock.call_args.kwargs["env"],
            {"TORKIT_BUILD_ID": "build-123"},
        )

    def test_require_detects_rootless_engine(self) -> None:
        client = self.make_client()
        results = [
            subprocess.CompletedProcess(args=[], returncode=0, stdout="v5.3.1"),
            subprocess.CompletedProcess(
                args=[], returncode=0,
                stdout='["name=seccomp,profile=builtin","name=rootless","name=cgroupns"]',
            ),
        ]
        with patch("torkit_host.docker.shutil.which", return_value="/usr/bin/docker"):
            with patch("torkit_host.docker.run", side_effect=results) as runner:
                client.require()
        self.assertTrue(client.rootless)
        self.assertEqual(
            runner.call_args_list[-1].args[0],
            ["docker", "info", "--format", "{{json .SecurityOptions}}"],
        )

    def test_require_keeps_regular_engine_user_mode(self) -> None:
        client = self.make_client()
        results = [
            subprocess.CompletedProcess(args=[], returncode=0, stdout="v5.3.1"),
            subprocess.CompletedProcess(
                args=[], returncode=0,
                stdout='["name=seccomp,profile=builtin","name=cgroupns"]',
            ),
        ]
        with patch("torkit_host.docker.shutil.which", return_value="/usr/bin/docker"):
            with patch("torkit_host.docker.run", side_effect=results):
                client.require()
        self.assertFalse(client.rootless)

    def test_absent_container_has_absent_status(self) -> None:
        client = self.make_client()
        with patch.object(client, "_container_id", return_value=""):
            self.assertEqual(client.status(SERVICES["chat"]), "absent")

    def test_health_without_healthcheck_is_none(self) -> None:
        client = self.make_client()
        result = subprocess.CompletedProcess(args=[], returncode=0, stdout="none\n")

        with patch.object(client, "_container_id", return_value="container-id"):
            with patch("torkit_host.docker.run", return_value=result):
                self.assertEqual(client.health(SERVICES["chat"]), "none")

    def test_mount_source_returns_inspected_source(self) -> None:
        client = self.make_client()
        result = subprocess.CompletedProcess(
            args=[],
            returncode=0,
            stdout="/home/test/onion_share\n",
        )

        with patch.object(client, "_container_id", return_value="container-id"):
            with patch("torkit_host.docker.run", return_value=result) as run_mock:
                source = client.mount_source(
                    SERVICES["share"],
                    "/srv/torkit/share",
                )

        self.assertEqual(source, "/home/test/onion_share")
        template = run_mock.call_args.args[0][3]
        self.assertIn("/srv/torkit/share", template)

    def test_container_info_parses_common_inspection(self) -> None:
        client = self.make_client()
        summary = (
            "/torkit-chat-1|running|healthy|2|"
            "2026-08-03T18:00:00Z|torkit:local"
        )

        with patch.object(client, "_inspect", return_value=summary) as inspect_mock:
            info = client.container_info(SERVICES["chat"])

        inspect_mock.assert_called_once()
        self.assertEqual(
            info,
            ContainerInfo(
                name="torkit-chat-1",
                state="running",
                health="healthy",
                restart_count="2",
                started_at="2026-08-03T18:00:00Z",
                image="torkit:local",
            ),
        )

    def test_malformed_container_info_returns_none(self) -> None:
        client = self.make_client()
        with patch.object(client, "_inspect", return_value="bad data"):
            self.assertIsNone(client.container_info(SERVICES["chat"]))

    def test_stale_image_build_id_triggers_build(self) -> None:
        client = self.make_client()
        with patch.object(client, "_image_build_id", return_value="old"):
            with patch.object(client, "build") as build_mock:
                client.ensure_image()
        build_mock.assert_called_once_with()

    def test_matching_image_build_id_skips_build(self) -> None:
        client = self.make_client()
        with patch.object(client, "_image_build_id", return_value="build-123"):
            with patch.object(client, "build") as build_mock:
                client.ensure_image()
        build_mock.assert_not_called()


if __name__ == "__main__":
    unittest.main()
