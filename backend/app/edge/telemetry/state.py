"""Latest accepted telemetry tick per machine.

Holds live machine state for checks that need "what is the machine doing
now", such as the parked check for training. Only live ingest writes it.
Historical batch rows in edge.telemetry are not current state, so the
database is not used for this.
"""
from __future__ import annotations

import threading

from app.schemas.telemetry import TelemetryTick

_lock = threading.Lock()
_latest: dict[str, TelemetryTick] = {}


def record_tick(tick: TelemetryTick) -> None:
    with _lock:
        _latest[tick.machine_id] = tick


def latest_tick(machine_id: str) -> TelemetryTick | None:
    with _lock:
        return _latest.get(machine_id)


def clear(machine_id: str | None = None) -> None:
    """Forget live state, for one machine or all. Called when streaming stops."""
    with _lock:
        if machine_id is None:
            _latest.clear()
        else:
            _latest.pop(machine_id, None)
