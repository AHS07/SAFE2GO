"""Cloud connectivity flag.

Controls whether the sync service delivers messages between tiers.
When cloud_reachable is False:
- Outbound messages stay in the outbox.
- Cloud-only features (DeepSeek, new assignment delivery) report unavailable.
- The safety engine and edge features continue unaffected.
"""
from __future__ import annotations

import threading


class ConnectivityState:
    """Thread-safe cloud reachability flag."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._reachable: bool = True

    @property
    def cloud_reachable(self) -> bool:
        with self._lock:
            return self._reachable

    def set_reachable(self, value: bool) -> None:
        with self._lock:
            self._reachable = value


# Module-level singleton.
_state = ConnectivityState()


def is_cloud_reachable() -> bool:
    """Return True if the cloud tier is currently reachable."""
    return _state.cloud_reachable


def set_cloud_reachable(value: bool) -> None:
    """Set the cloud reachability flag. Called by the connectivity toggle endpoint."""
    _state.set_reachable(value)
