# -*- coding: utf-8 -*-
"""
Onion service routing models.
"""

from dataclasses import dataclass


@dataclass(frozen=True)
class OnionPortMapping:
    """
    Map a port exposed by an onion service to a local TCP service.
    """

    virtual_port: int
    target_host: str
    target_port: int

    def __post_init__(self):
        if not 1 <= self.virtual_port <= 65535:
            raise ValueError("virtual_port must be between 1 and 65535")

        if not 1 <= self.target_port <= 65535:
            raise ValueError("target_port must be between 1 and 65535")

        if not self.target_host:
            raise ValueError("target_host must not be empty")

    def stem_target(self):
        """
        Return the target format expected by Stem.

        Preserve OnionShare's existing integer target for IPv4 localhost.
        """

        if self.target_host == "127.0.0.1":
            return self.target_port

        if ":" in self.target_host:
            return f"[{self.target_host}]:{self.target_port}"

        return f"{self.target_host}:{self.target_port}"
