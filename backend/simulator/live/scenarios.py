"""Scenario injection for the live demo.

Each scenario queues a short window of raw sensor overrides that the live
stream applies to its ticks. The safety and behavior engines react to the
raw values; the simulator never emits alert flags.

A window targets one machine, or every streamed machine when machine_id
is None.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime, timedelta

from app.core.clock import sim_now

log = logging.getLogger("safe2go.scenarios")


@dataclass
class ScenarioWindow:
    name: str
    start: datetime
    end: datetime
    overrides: dict   # sensor field overrides applied during this window
    machine_id: str | None = None

    def applies_to(self, machine_id: str | None, ts: datetime) -> bool:
        in_time = self.start <= ts < self.end
        return in_time and (self.machine_id is None or self.machine_id == machine_id)


class ScenarioInjector:
    """Manages active scenario windows for the live stream."""

    def __init__(self) -> None:
        self._windows: list[ScenarioWindow] = []

    def active_overrides(self, ts: datetime, machine_id: str | None = None) -> dict:
        """Merged sensor overrides for this machine at the given timestamp."""
        merged: dict = {}
        for window in self._windows:
            if window.applies_to(machine_id, ts):
                merged.update(window.overrides)
        self._windows = [w for w in self._windows if w.end > ts]
        return merged

    def active_windows(self, ts: datetime) -> list[ScenarioWindow]:
        return [w for w in self._windows if w.start <= ts < w.end]

    def clear(self) -> None:
        self._windows = []

    def inject(
        self,
        name: str,
        duration_seconds: int,
        overrides: dict,
        machine_id: str | None = None,
    ) -> ScenarioWindow:
        now = sim_now()
        window = ScenarioWindow(
            name=name,
            start=now,
            end=now + timedelta(seconds=duration_seconds),
            overrides=overrides,
            machine_id=machine_id,
        )
        self._windows.append(window)
        log.info(
            "Scenario injected",
            extra={"scenario_name": name, "duration_s": duration_seconds, "machine_id": machine_id},
        )
        return window

    # ------------------------------------------------------------------
    # Named scenarios
    # ------------------------------------------------------------------

    def proximity_breach(
        self, distance_m: float = 4.0, duration_seconds: int = 60, machine_id: str | None = None
    ) -> ScenarioWindow:
        """Force proximity_distance below the critical threshold."""
        return self.inject(
            "proximity_breach", duration_seconds, {"proximity_distance": distance_m}, machine_id
        )

    def overload(
        self, payload_pct: float = 125.0, duration_seconds: int = 120, machine_id: str | None = None
    ) -> ScenarioWindow:
        """Force payload_pct above the critical overload threshold."""
        return self.inject(
            "overload",
            duration_seconds,
            {"payload_pct": payload_pct, "hydraulic_active": True},
            machine_id,
        )

    def seatbelt_removal(
        self, duration_seconds: int = 45, machine_id: str | None = None
    ) -> ScenarioWindow:
        """Remove the seatbelt while the machine can move."""
        return self.inject(
            "seatbelt_removal",
            duration_seconds,
            {"seatbelt_status": "unfastened", "park_brake": False, "gear_state": "forward"},
            machine_id,
        )

    def excessive_idling(
        self, duration_seconds: int = 400, machine_id: str | None = None
    ) -> ScenarioWindow:
        """Engine running, no hydraulic activity, speed 0."""
        return self.inject(
            "excessive_idling",
            duration_seconds,
            {
                "hydraulic_active": False,
                "machine_speed": 0.0,
                "payload_pct": 0.0,
                "gear_state": "neutral",
            },
            machine_id,
        )

    def tilt(
        self, tilt_angle: float = 31.0, duration_seconds: int = 30, machine_id: str | None = None
    ) -> ScenarioWindow:
        """Force tilt_angle above the machine tilt limit."""
        return self.inject("tilt", duration_seconds, {"tilt_angle": tilt_angle}, machine_id)


# Module-level singleton used by the API routes.
scenario_injector = ScenarioInjector()
