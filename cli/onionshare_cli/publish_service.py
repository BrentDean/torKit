# -*- coding: utf-8 -*-
"""
Publish an existing loopback TCP service through an onion service.
"""

from __future__ import annotations

import argparse
import ipaddress
import socket
import time
from dataclasses import dataclass

from .onion_service import OnionPortMapping


@dataclass(frozen=True)
class ServiceTarget:
    """A validated loopback TCP service target."""

    host: str
    port: int

    @property
    def address(self) -> str:
        return f"{self.host}:{self.port}"


def parse_service_target(value: str) -> ServiceTarget:
    """Parse HOST:PORT and require an IPv4 loopback address."""

    try:
        host, port_text = value.rsplit(":", 1)
    except ValueError as exc:
        raise argparse.ArgumentTypeError(
            "Expected HOST:PORT, for example 127.0.0.1:8000"
        ) from exc

    try:
        port = int(port_text)
    except ValueError as exc:
        raise argparse.ArgumentTypeError(
            "The target port must be a number"
        ) from exc

    if not 1 <= port <= 65535:
        raise argparse.ArgumentTypeError(
            "The target port must be between 1 and 65535"
        )

    try:
        address = ipaddress.ip_address(host)
    except ValueError as exc:
        raise argparse.ArgumentTypeError(
            "The target host must be a loopback IP address"
        ) from exc

    if address.version != 4 or not address.is_loopback:
        raise argparse.ArgumentTypeError(
            "Only IPv4 loopback targets are currently allowed"
        )

    return ServiceTarget(host=host, port=port)


def require_listening_service(
    target: ServiceTarget,
    timeout: float = 1.0,
) -> None:
    """Require a TCP listener before creating an onion service."""

    try:
        with socket.create_connection(
            (target.host, target.port),
            timeout=timeout,
        ):
            return
    except OSError as exc:
        raise RuntimeError(
            f"No TCP service is listening on {target.address}"
        ) from exc

def run_publish_service(
    app,
    mode_settings,
    target: ServiceTarget,
    virtual_port: int = 80,
) -> None:
    """Publish one existing loopback TCP service until interrupted."""

    mapping = OnionPortMapping(
        virtual_port=virtual_port,
        target_host=target.host,
        target_port=target.port,
    )

    app.start_onion_service(
        "local_service",
        mode_settings,
        port_mappings=[mapping],
    )

    url = f"http://{app.onion_host}"

    print("")
    print("Local service published")
    print(f"Backend: {target.address}")
    print(f"Onion address: {url}")

    if not mode_settings.get("general", "public"):
        print(f"Private key: {app.auth_string}")

    print("")
    print("Press Ctrl+C to stop publishing the service")

    while True:
        time.sleep(0.2)
