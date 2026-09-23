"""Parked check for training content (prd.md F5.2).

Parked means the latest live tick shows the park brake on and zero speed.
No live tick means the machine state is unknown, and unknown is never
treated as safe, so content stays blocked.
"""
from __future__ import annotations

from app.core.errors import MachineNotParkedError
from app.edge.telemetry.state import latest_tick
from app.schemas.telemetry import TelemetryTick


def is_parked(tick: TelemetryTick | None) -> bool:
    return tick is not None and tick.park_brake and tick.machine_speed == 0.0


def machine_is_parked(machine_id: str) -> bool:
    return is_parked(latest_tick(machine_id))


def require_parked(machine_id: str) -> None:
    tick = latest_tick(machine_id)
    if tick is None:
        raise MachineNotParkedError(
            "Machine state is unknown. Training opens when the machine reports it is parked.",
            details={"machine_id": machine_id, "reason": "no_telemetry"},
        )
    if not is_parked(tick):
        raise MachineNotParkedError(
            "Training is available only while parked. Stop and set the park brake.",
            details={
                "machine_id": machine_id,
                "machine_speed": tick.machine_speed,
                "park_brake": tick.park_brake,
            },
        )
