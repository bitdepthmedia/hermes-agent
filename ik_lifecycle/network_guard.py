"""Fail-closed network-denial adapter boundary.

The default adapter does not claim enforcement. Production callers must supply
an OS-backed adapter with independently verified isolation evidence.
"""

from __future__ import annotations

from dataclasses import dataclass

from .models import LifecycleBlockedError


@dataclass(frozen=True)
class DeniedNetworkAdapter:
    enforced: bool = False
    adapter_id: str = "synthetic-fixture"

    def execute(self, argv: tuple[str, ...]) -> int:
        if not self.enforced:
            raise LifecycleBlockedError("network_denial_unproven", "network denial is not enforced")
        if not argv or argv[0] != "synthetic":
            raise LifecycleBlockedError("network_command_not_fixture", "network adapter refuses non-fixture command")
        return 0


@dataclass(frozen=True)
class NetworkDeniedReceipt:
    status: str
    adapter_id: str
    argv: tuple[str, ...]


def run_network_denied(argv: tuple[str, ...], adapter: DeniedNetworkAdapter) -> NetworkDeniedReceipt:
    if not adapter.enforced:
        raise LifecycleBlockedError("network_denial_unproven", "network denial enforcement proof is required")
    if adapter.execute(argv) != 0:
        raise LifecycleBlockedError("network_denied_command_failed", "network-denied command failed")
    return NetworkDeniedReceipt("CLEAR", adapter.adapter_id, argv)
