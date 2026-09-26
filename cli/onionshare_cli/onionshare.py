# -*- coding: utf-8 -*-
"""
OnionShare | https://onionshare.org/

Copyright (C) 2014-2022 Micah Lee, et al. <micah@micahflee.com>

This program is free software: you can redistribute it and/or modify
it under the terms of the GNU General Public License as published by
the Free Software Foundation, either version 3 of the License, or
(at your option) any later version.

This program is distributed in the hope that it will be useful,
but WITHOUT ANY WARRANTY; without even the implied warranty of
MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
GNU General Public License for more details.

You should have received a copy of the GNU General Public License
along with this program.  If not, see <http://www.gnu.org/licenses/>.
"""

import os

from .common import AutoStopTimer
from .onion_service import OnionPortMapping


class OnionShare(object):
    """
    OnionShare is the main application class. Pass in options and run
    start_onion_service and it will do the magic.
    """

    def __init__(self, common, onion, local_only=False, autostop_timer=0):
        self.common = common

        self.common.log("OnionShare", "__init__")

        # The Onion object
        self.onion = onion

        self.hidserv_dir = None
        self.onion_host = None
        self.port = None

        # do not use tor -- for development
        self.local_only = local_only

        # optionally shut down after N hours
        self.autostop_timer = autostop_timer
        # init auto-stop timer thread
        self.autostop_timer_thread = None

    def choose_port(self):
        """
        Choose a random port.
        """
        try:
            self.port = self.common.get_available_port(17600, 17650)
        except Exception:
            raise OSError("Cannot find an available OnionShare port")

    def start_onion_service(
        self,
        mode,
        mode_settings,
        await_publication=True,
        port_mappings=None,
    ):
        """
        Start an onion service.

        Existing OnionShare modes use an OnionShare-owned random local port.
        TorKit-managed services may supply mappings to existing listeners.
        """
        self.common.log("OnionShare", "start_onion_service")

        if port_mappings is None:
            if not self.port:
                self.choose_port()

            port_mappings = [
                OnionPortMapping(
                    virtual_port=80,
                    target_host="127.0.0.1",
                    target_port=self.port,
                )
            ]
        elif not port_mappings:
            raise ValueError(
                "At least one onion port mapping is required"
            )

        if self.autostop_timer > 0:
            self.autostop_timer_thread = AutoStopTimer(self.common, self.autostop_timer)

        if self.local_only:
            first_mapping = port_mappings[0]
            self.onion_host = (
                f"{first_mapping.target_host}:"
                f"{first_mapping.target_port}"
            )
            if not mode_settings.get("general", "public"):
                self.auth_string = (
                    "E2GOT5LTUTP3OAMRCRXO4GSH6VKJEUOXZQUC336SRKAHTTT5OVSA"
                )
            return

        self.onion_host = self.onion.start_onion_service(
            mode,
            mode_settings,
            port_mappings,
            await_publication,
        )

        if not mode_settings.get("general", "public"):
            self.auth_string = self.onion.auth_string

    def stop_onion_service(self, mode_settings):
        """
        Stop the onion service
        """
        self.onion.stop_onion_service(mode_settings)
