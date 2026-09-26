import argparse
import socket

import pytest

from onionshare_cli.publish_service import (
    ServiceTarget,
    parse_service_target,
    require_listening_service,
)


def test_parse_service_target():
    assert parse_service_target(
        "127.0.0.1:8000"
    ) == ServiceTarget(
        host="127.0.0.1",
        port=8000,
    )


@pytest.mark.parametrize(
    "value",
    [
        "127.0.0.1",
        "127.0.0.1:not-a-port",
        "127.0.0.1:0",
        "127.0.0.1:65536",
        "192.168.1.20:8000",
        "localhost:8000",
        "[::1]:8000",
    ],
)
def test_parse_service_target_rejects_invalid_values(value):
    with pytest.raises(argparse.ArgumentTypeError):
        parse_service_target(value)


def test_require_listening_service_accepts_open_port():
    listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    listener.bind(("127.0.0.1", 0))
    listener.listen(1)

    try:
        host, port = listener.getsockname()
        require_listening_service(
            ServiceTarget(
                host=host,
                port=port,
            )
        )
    finally:
        listener.close()


def test_require_listening_service_rejects_closed_port():
    temporary_listener = socket.socket(
        socket.AF_INET,
        socket.SOCK_STREAM,
    )
    temporary_listener.bind(("127.0.0.1", 0))
    host, port = temporary_listener.getsockname()
    temporary_listener.close()

    with pytest.raises(
        RuntimeError,
        match="No TCP service is listening",
    ):
        require_listening_service(
            ServiceTarget(
                host=host,
                port=port,
            )
        )
