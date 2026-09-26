from __future__ import annotations

from contextlib import redirect_stdout
from io import StringIO
import unittest
from unittest.mock import patch

from torkit_host.cli import _service_arguments, burn, down_menu, nuke, reset_menu, restart_menu, up_menu
from torkit_host.model import SERVICES, Service, TorKitError


class FakeRuntime:
    def __init__(
        self,
        *,
        statuses: dict[str, str] | None = None,
        ready: dict[str, bool] | None = None,
    ) -> None:
        self.statuses = statuses or {}
        self.ready_values = ready or {}
        self.started: list[Service] = []
        self.stopped: list[Service] = []
        self.restarted: list[Service] = []
        self.reset: list[Service] = []
        self.nuked = False
        self.burned: list[Service] = []

    def status(self, service: Service) -> str:
        return self.statuses.get(service.name, "absent")

    def ready(self, service: Service) -> bool:
        return self.ready_values.get(service.name, False)

    def value(self, service: Service) -> str:
        return service.default

    def active_value(self, service: Service) -> str:
        return service.default

    def start(self, services: list[Service]) -> None:
        self.started = list(services)

    def stop_many(self, services: list[Service]) -> None:
        self.stopped = list(services)

    def restart_many(self, services: list[Service]) -> None:
        self.restarted = list(services)

    def reset_identities(self, services: list[Service]) -> None:
        self.reset = list(services)

    def nuke(self) -> None:
        self.nuked = True

    def burn(self, services: list[Service]) -> None:
        self.burned = list(services)


class HostCliTests(unittest.TestCase):
    def capture(self, function, runtime, *responses: str) -> str:
        output = StringIO()
        with patch("builtins.input", side_effect=responses), redirect_stdout(output):
            function(runtime)
        return output.getvalue()

    def test_machine_service_selection(self) -> None:
        self.assertEqual(
            [service.name for service in _service_arguments(["chat", "website", "chat"])],
            ["chat", "website"],
        )
        self.assertEqual(
            [service.name for service in _service_arguments(["all"])],
            list(SERVICES),
        )
        self.assertEqual(
            [service.name for service in _service_arguments(["messageboard"])],
            ["board"],
        )
        with self.assertRaises(TorKitError):
            _service_arguments([])
        with self.assertRaises(TorKitError):
            _service_arguments(["all", "chat"])
        with self.assertRaises(TorKitError):
            _service_arguments(["unknown"])

    def test_up_all_starts_only_services_not_ready(self) -> None:
        runtime = FakeRuntime(
            statuses={"chat": "running", "website": "running"},
            ready={"website": True},
        )
        output = self.capture(up_menu, runtime, "a")
        self.assertEqual(
            [service.name for service in runtime.started],
            ["board", "share", "receive"],
        )
        self.assertIn("a. All", output)
        self.assertIn("q. Exit", output)

    def test_down_stops_selected_active_service(self) -> None:
        runtime = FakeRuntime(
            statuses={"chat": "running", "share": "restarting", "receive": "exited"}
        )
        output = self.capture(down_menu, runtime, "1")
        self.assertEqual(
            [service.name for service in runtime.stopped],
            ["chat"],
        )
        self.assertIn("[running]", output)
        self.assertIn("a. All", output)
        self.assertIn("q. Exit", output)

    def test_down_all_stops_all_active_services(self) -> None:
        runtime = FakeRuntime(
            statuses={"chat": "running", "share": "restarting", "receive": "exited"}
        )
        self.capture(down_menu, runtime, "a")
        self.assertEqual(
            [service.name for service in runtime.stopped],
            ["chat", "share"],
        )

    def test_restart_all_restarts_all_running_services(self) -> None:
        runtime = FakeRuntime(statuses={"chat": "running", "share": "running"})
        output = self.capture(restart_menu, runtime, "a")
        self.assertEqual(
            [service.name for service in runtime.restarted],
            ["chat", "share"],
        )
        self.assertIn("a. All", output)

    def test_reset_all_requires_confirmation(self) -> None:
        runtime = FakeRuntime(statuses={"chat": "running"})
        self.capture(reset_menu, runtime, "a", "n")
        self.assertEqual(runtime.reset, [])

        self.capture(reset_menu, runtime, "a", "y")
        self.assertEqual(
            [service.name for service in runtime.reset],
            list(SERVICES),
        )

    def test_burn_requires_word_case_insensitive(self) -> None:
        runtime = FakeRuntime()
        selected = [SERVICES["chat"]]
        with patch("builtins.input", return_value="wrong"):
            with redirect_stdout(StringIO()):
                burn(runtime, selected)
        self.assertEqual(runtime.burned, [])
        with patch("builtins.input", return_value="bUrN"):
            with redirect_stdout(StringIO()):
                burn(runtime, selected)
        self.assertEqual(runtime.burned, selected)

    def test_nuke_requires_word_case_insensitive(self) -> None:
        runtime = FakeRuntime()
        self.capture(nuke, runtime, "wrong")
        self.assertFalse(runtime.nuked)
        self.capture(nuke, runtime, "nUkE")
        self.assertTrue(runtime.nuked)


if __name__ == "__main__":
    unittest.main()
