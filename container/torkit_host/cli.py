from __future__ import annotations

import json
import sys
from typing import Callable, Iterable

from .config import ensure_private_state_dirs, load_config
from .model import CONFIG_FILE, SERVICES, Service, TorKitError
from .runtime import TorKitRuntime


QUIT_VALUES = {"q", "quit", "exit"}
ALL_VALUES = {"a", "all"}


def usage() -> str:
    return f"""torKit

Commands:
  ./torkit              Select services to start (same as ./torkit up)
  ./torkit up           Select services to start
  ./torkit chat         Start or resume disposable live Chat
  ./torkit board        Start or resume persistent message Board
  ./torkit messageboard Alias for ./torkit board
  ./torkit down         Select running services to stop; preserve identity
  ./torkit restart      Select running services to restart; preserve identity
  ./torkit reset        Select services to give new addresses and keys
  ./torkit burn SERVICE Burn selected service identities and runtime state
  ./torkit operator     Start the localhost-only operator console
  ./torkit nuke         Destroy all TorKit containers, image, and identities
  ./torkit ps           Show Docker container diagnostics
  ./torkit status       Show running onion services and access keys
  ./torkit ensure all   Reconcile all services without prompting or printing keys
  ./torkit ensure chat website  Reconcile selected services without prompting
  ./torkit health --json  Print credential-free machine-readable health
  ./torkit startup-check  Check rootless Docker boot prerequisites
  ./torkit backup [--no-content] [--prune]  Encrypt a consistent off-host snapshot
  ./torkit snapshots    List TorKit recovery snapshots
  ./torkit backup-check Verify encrypted repository data
  ./torkit restore SNAPSHOT [--replace]  Restore with explicit confirmation

Configuration:
  {CONFIG_FILE}

Edit that file to change the chat name or service directories.
Docker is the default runtime. Onion client authorization is enabled.
"""


def _parse_number_selection(
    selection: str,
    numbered_services: list[Service],
) -> list[Service]:
    chosen: list[Service] = []
    valid_numbers = {str(index) for index in range(1, len(numbered_services) + 1)}

    for token in selection.replace(",", " ").split():
        if token not in valid_numbers:
            raise TorKitError(f"invalid selection: {token}")
        service = numbered_services[int(token) - 1]
        if service not in chosen:
            chosen.append(service)

    return chosen


def _services_in_states(
    runtime: TorKitRuntime,
    *states: str,
) -> list[Service]:
    accepted = set(states)
    return [
        service
        for service in SERVICES.values()
        if runtime.status(service) in accepted
    ]


def _status_marker(status: str) -> str:
    if status in {"running", "restarting"}:
        return "[running]"
    if status == "absent":
        return "[not created]"
    return "[stopped]"


def _service_menu(
    *,
    title: str,
    services: Iterable[Service],
    value: Callable[[Service], str],
    default: int | None = None,
    marker: Callable[[Service], str] | None = None,
    note: str | None = None,
) -> list[Service] | None:
    numbered = list(services)

    print("torKit\n")
    print(f"{title}:\n")

    label_width = 10
    value_width = 40

    for index, service in enumerate(numbered, 1):
        displayed_value = value(service)
        displayed_marker = marker(service) if marker else ""

        line = (
            f"  {index}. "
            f"{service.label:<{label_width}}"
            f"{displayed_value:<{value_width}}"
            f"{displayed_marker}"
        )
        print(line.rstrip())

    print("  a. All")
    print("  q. Exit")

    if note:
        print(f"\n{note}")

    prompt = "Selection"
    if default is not None:
        prompt += f" [{default}]"

    selection = input(f"\n{prompt}: ").strip()

    if selection.lower() in QUIT_VALUES:
        return None

    if not selection and default is None:
        return None

    selection = selection or str(default)

    if selection.lower() in ALL_VALUES:
        return numbered

    return _parse_number_selection(selection, numbered)


def _service_arguments(values: list[str]) -> list[Service]:
    if not values:
        raise TorKitError("specify one or more services or 'all'")
    if values == ["all"]:
        return list(SERVICES.values())
    if "all" in values:
        raise TorKitError("'all' cannot be combined with individual services")
    from .config import normalize_service

    selected: list[Service] = []
    for value in values:
        service = normalize_service(value)
        if service not in selected:
            selected.append(service)
    return selected


def up_menu(runtime: TorKitRuntime) -> None:
    services = list(SERVICES.values())
    statuses = {service.name: runtime.status(service) for service in services}
    ready = {service.name: runtime.ready(service) for service in services}
    default = next(
        (
            index
            for index, service in enumerate(services, 1)
            if statuses[service.name] != "running"
        ),
        None,
    )

    def marker(service: Service) -> str:
        status = statuses[service.name]
        if ready[service.name]:
            return "[running]"
        if status in {"running", "restarting"}:
            return "[restart required]"
        return _status_marker(status)

    if default is None:
        print("torKit\n")
        print("All TorKit services are running.")
        print("Run './torkit status' for access information or './torkit restart'.")
        return

    chosen = _service_menu(
        title="Select services to start",
        services=services,
        value=runtime.value,
        default=default,
        marker=marker,
        note=f"To change names or directories, edit:\n  {CONFIG_FILE}",
    )

    if chosen is None:
        print("No services started.")
        return

    targets = [
        service for service in chosen if statuses[service.name] != "running"
    ]
    for service in chosen:
        if statuses[service.name] == "running":
            if ready[service.name]:
                print(f"{service.label} is already running; skipping.")
            else:
                print(
                    f"{service.label} requires restart; "
                    "run './torkit restart'."
                )

    if not targets:
        print("No services started.")
        return

    print()
    runtime.start(targets)


def down_menu(runtime: TorKitRuntime) -> None:
    running = _services_in_states(runtime, "running", "restarting")
    if not running:
        print("No TorKit services are running.")
        return

    chosen = _service_menu(
        title="Select services to stop",
        services=running,
        value=runtime.active_value,
        marker=lambda _service: "[running]",
    )
    if chosen is None:
        print("No services stopped.")
        return

    runtime.stop_many(chosen)


def restart_menu(runtime: TorKitRuntime) -> None:
    running = _services_in_states(runtime, "running")
    if not running:
        print("No TorKit services are running.")
        return

    chosen = _service_menu(
        title="Select services to restart",
        services=running,
        value=runtime.active_value,
        marker=lambda _service: "[running]",
    )
    if chosen is None:
        print("No services restarted.")
        return

    runtime.restart_many(chosen)


def reset_menu(runtime: TorKitRuntime) -> None:
    services = list(SERVICES.values())
    statuses = {service.name: runtime.status(service) for service in services}

    chosen = _service_menu(
        title="Select services to reset",
        services=services,
        value=runtime.value,
        marker=lambda service: _status_marker(statuses[service.name]),
        note=(
            "Resetting creates new onion addresses and access keys.\n"
            "Existing links for selected services will stop working."
        ),
    )
    if chosen is None:
        print("No services reset.")
        return

    labels = ", ".join(service.label for service in chosen)
    answer = input(
        f"\nReset {labels}? Files and configuration will be preserved. [y/N]: "
    ).strip()
    if answer.lower() not in {"y", "yes"}:
        print("No services reset.")
        return

    runtime.reset_identities(chosen)


def burn(runtime: TorKitRuntime, services: list[Service]) -> None:
    if not services:
        raise TorKitError("burn requires at least one service")
    labels = ", ".join(service.label for service in services)
    print(f"Burn {labels}? Selected onion identities and runtime state will be deleted.")
    print("Configuration and user content directories will be preserved.")
    if input("Type BURN to confirm: ").strip().casefold() != "burn":
        print("Burn cancelled.")
        return
    runtime.burn(services)


def nuke(runtime: TorKitRuntime) -> None:
    print(
        "NUKE TorKit runtime?\n\n"
        "This removes every TorKit container, image, onion address, access key,\n"
        "and runtime state. Configuration and service data will be preserved.\n"
    )
    answer = input("Type NUKE to continue: ").strip()
    if answer.casefold() != "nuke":
        print("Nuke cancelled.")
        return
    runtime.nuke()


def main(arguments: list[str]) -> int:
    if arguments and arguments[0] in {"-h", "--help", "help"}:
        if len(arguments) != 1:
            raise TorKitError("help does not accept extra arguments")
        print(usage(), end="")
        return 0

    # Recovery must run before ordinary initialization: restoring onto a fresh
    # host must not manufacture a new config or onion identity first.
    if arguments:
        from . import recovery
        if arguments == ["startup-check"]:
            recovery.startup_check()
            return 0
        if arguments[0] == "backup":
            options = arguments[1:]
            if len(options) != len(set(options)) or set(options) - {"--no-content", "--prune"}:
                raise TorKitError("usage: ./torkit backup [--no-content] [--prune]")
            recovery.backup(include_content="--no-content" not in options,
                            prune="--prune" in options)
            return 0
        if arguments == ["snapshots"]:
            recovery.snapshots()
            return 0
        if arguments == ["backup-check"]:
            recovery.backup_check()
            return 0
        if arguments[0] == "restore":
            options = arguments[1:]
            if (not options or len(options) > 2 or
                    options[0].startswith("-") or
                    (len(options) == 2 and options[1] != "--replace")):
                raise TorKitError("usage: ./torkit restore SNAPSHOT [--replace]")
            recovery.restore(options[0], replace="--replace" in options)
            return 0

    config = load_config()
    ensure_private_state_dirs()
    runtime = TorKitRuntime(config)
    runtime.require()

    if arguments in (["chat"], ["board"], ["messageboard"]):
        runtime.ensure(_service_arguments(arguments))
        return 0
    if arguments and arguments[0] == "ensure":
        runtime.ensure(_service_arguments(arguments[1:]))
        return 0
    if arguments in (["health"], ["health", "--json"]):
        snapshot = runtime.health_snapshot()
        if arguments == ["health", "--json"]:
            print(json.dumps(snapshot, sort_keys=True))
        else:
            for name, state in snapshot.items():
                print(
                    f"{name:<10} {state['state']:<12} "
                    f"{state['health']:<12} ready={state['ready']}"
                )
        return 0
    if not arguments or arguments == ["up"]:
        up_menu(runtime)
        return 0
    if arguments == ["down"]:
        down_menu(runtime)
        return 0
    if arguments == ["restart"]:
        restart_menu(runtime)
        return 0
    if arguments == ["reset"]:
        reset_menu(runtime)
        return 0
    if arguments and arguments[0] == "burn":
        burn(runtime, _service_arguments(arguments[1:]))
        return 0
    if arguments == ["operator"]:
        from .operator import serve_operator

        serve_operator(runtime)
        return 0
    if arguments == ["nuke"]:
        nuke(runtime)
        return 0
    if arguments == ["ps"]:
        runtime.ps()
        return 0
    if arguments == ["status"]:
        runtime.dashboard()
        return 0

    valid = (
        "./torkit, ./torkit up, ./torkit down, ./torkit restart, "
        "./torkit reset, ./torkit burn SERVICE..., ./torkit nuke, "
        "./torkit operator, ./torkit chat, ./torkit board, "
        "./torkit messageboard, ./torkit ps, ./torkit status, "
        "./torkit ensure SERVICE..., ./torkit health [--json], "
        "./torkit startup-check, ./torkit backup, ./torkit snapshots, "
        "./torkit backup-check, ./torkit restore SNAPSHOT [--replace]"
    )
    raise TorKitError(f"unknown command; use one of: {valid}")


def entrypoint() -> None:
    try:
        raise SystemExit(main(sys.argv[1:]))
    except TorKitError as exc:
        print(f"torKit: {exc}", file=sys.stderr)
        raise SystemExit(1)
    except KeyboardInterrupt:
        print()
        raise SystemExit(130)
