import os

import pytest

from onionshare_cli import OnionShare
from onionshare_cli.common import Common
from onionshare_cli.mode_settings import ModeSettings
from onionshare_cli.onion_service import OnionPortMapping


class MyOnion:
    def __init__(self):
        self.auth_string = "TestHidServAuth"
        self.private_key = ""
        self.received_port_mappings = None
        self.scheduled_key = None

    def start_onion_service(
        self,
        mode,
        mode_settings_obj,
        port_mappings,
        await_publication=True,
    ):
        self.received_port_mappings = port_mappings
        return "test_service_id.onion"


@pytest.fixture
def onionshare_obj():
    common = Common()
    return OnionShare(common, MyOnion())


@pytest.fixture
def mode_settings_obj():
    common = Common()
    return ModeSettings(common)


class TestOnionShare:
    def test_init(self, onionshare_obj):
        assert onionshare_obj.hidserv_dir is None
        assert onionshare_obj.onion_host is None
        assert onionshare_obj.local_only is False

    def test_start_onion_service(self, onionshare_obj, mode_settings_obj):
        onionshare_obj.start_onion_service("share", mode_settings_obj)
        assert 17600 <= onionshare_obj.port <= 17650
        assert onionshare_obj.onion_host == "test_service_id.onion"
        assert onionshare_obj.onion.received_port_mappings == [
            OnionPortMapping(
                virtual_port=80,
                target_host="127.0.0.1",
                target_port=onionshare_obj.port,
            )
        ]

    def test_start_onion_service_with_existing_local_service(
        self, onionshare_obj, mode_settings_obj
    ):
        port_mappings = [
            OnionPortMapping(
                virtual_port=80,
                target_host="127.0.0.1",
                target_port=8000,
            )
        ]

        onionshare_obj.start_onion_service(
            "local_service",
            mode_settings_obj,
            port_mappings=port_mappings,
        )

        assert onionshare_obj.port is None
        assert onionshare_obj.onion_host == "test_service_id.onion"
        assert onionshare_obj.onion.received_port_mappings == port_mappings

    def test_start_onion_service_local_only(self, onionshare_obj, mode_settings_obj):
        onionshare_obj.local_only = True
        onionshare_obj.start_onion_service("share", mode_settings_obj)
        assert onionshare_obj.onion_host == "127.0.0.1:{}".format(onionshare_obj.port)


def test_onion_port_mapping_stem_target():
    assert OnionPortMapping(80, "127.0.0.1", 8000).stem_target() == 8000
    assert (
        OnionPortMapping(80, "::1", 8000).stem_target()
        == "[::1]:8000"
    )
