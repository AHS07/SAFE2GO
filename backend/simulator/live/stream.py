"""Live telemetry stream for one machine.

Builds one raw sensor tick per simulation-clock step from the machine's
current shift and in-progress task, then applies any active scenario
overrides. Ticks carry raw state only; the edge engines derive every
alert and event.

While a task is in progress the park brake is off and load cycles
complete at the pace given by the batch work model, so live progress
behaves like the historical data the ETA model was trained on. Every few
cycles the machine repositions: a short, slow travel with the implement
idle. With no task in progress the machine is parked and idling.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime

import numpy as np
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models.edge.assignment import EdgeMachine, EdgeShift, EdgeTask
from app.edge.shift import current_shift_for_machine
from app.edge.telemetry.ingest import validate_raw
from app.schemas.telemetry import TelemetryTick
from app.shared.enums import (
    MachineType,
    MaterialType,
    SkillLevel,
    TaskStatus,
    TaskType,
    WeatherCategory,
)
from simulator.batch.work_model import CYCLE_FILL_PCT_RANGE, cycle_seconds
from simulator.live.scenarios import scenario_injector

log = logging.getLogger("safe2go.live_stream")

# Engine speed while working, as a fraction of rated_max_rpm.
_WORK_RPM_FRACTION = (0.55, 0.80)
_IDLE_RPM_RANGE = (600.0, 800.0)
# Instantaneous payload while a cycle is in progress (bucket filling).
_FILLING_PAYLOAD_PCT = (0.0, 80.0)
_WORK_FUEL_L_PER_HOUR = 12.0
_IDLE_FUEL_L_PER_HOUR = 3.5
_AMBIENT_TEMP_C = (25.0, 32.0)
_VISIBILITY_M = (200.0, 600.0)
_TILT_FRACTION_OF_LIMIT = 0.4
_SECONDS_PER_HOUR = 3600.0
# Nominal conditions for the live demo: no per-task random noise.
_LIVE_CYCLE_NOISE = 1.0
# Repositioning between digging positions. The speed stays below every
# machine type's high-RPM travel threshold, so it never reads as misuse.
_REPOSITION_EVERY_CYCLES = 5
_REPOSITION_SECONDS = 20
_REPOSITION_SPEED_KMH = 2.0


@dataclass
class _CycleState:
    task_id: str
    cycle_seconds: int
    elapsed: int = 0
    completed: int = 0
    travel_left: int = 0


class LiveStream:
    """Builds ticks for one machine. Call next_tick once per clock step."""

    def __init__(self, machine_id: str, seed: int) -> None:
        self._machine_id = machine_id
        self._rng = np.random.default_rng(seed)
        self._engine_hours: float | None = None
        self._fuel_used = 0.0
        self._cycle: _CycleState | None = None

    @property
    def machine_id(self) -> str:
        return self._machine_id

    async def next_tick(self, ts: datetime, session: AsyncSession) -> TelemetryTick | None:
        machine = await session.get(EdgeMachine, self._machine_id)
        shift = await current_shift_for_machine(session, self._machine_id)
        if machine is None or shift is None:
            log.warning("No machine or shift, tick skipped", extra={"machine_id": self._machine_id})
            return None

        task = await self._active_task(session, shift.shift_id)
        # Same validation as any telemetry source: a malformed tick is counted
        # and dropped, never passed on.
        return validate_raw(self._build_tick(ts, machine, shift, task))

    def _build_tick(self, ts: datetime, machine: EdgeMachine, shift: EdgeShift, task: EdgeTask | None) -> dict:
        rng = self._rng
        working = task is not None

        if self._engine_hours is None:
            self._engine_hours = machine.engine_hours
        self._engine_hours += 1.0 / _SECONDS_PER_HOUR
        fuel_rate = _WORK_FUEL_L_PER_HOUR if working else _IDLE_FUEL_L_PER_HOUR
        self._fuel_used += fuel_rate / _SECONDS_PER_HOUR

        rpm_range = (
            (machine.rated_max_rpm * _WORK_RPM_FRACTION[0], machine.rated_max_rpm * _WORK_RPM_FRACTION[1])
            if working
            else _IDLE_RPM_RANGE
        )
        tick = {
            "timestamp": ts,
            "machine_id": machine.machine_id,
            "operator_id": shift.operator_id,
            "task_id": task.task_id if task else None,
            "shift_id": shift.shift_id,
            "engine_running": True,
            "engine_rpm": round(float(rng.uniform(*rpm_range)), 1),
            "engine_hours": round(self._engine_hours, 4),
            "fuel_used": round(self._fuel_used, 4),
            "hydraulic_active": working,
            "machine_speed": 0.0,
            "load_cycles": 0,
            "payload_pct": round(float(rng.uniform(*_FILLING_PAYLOAD_PCT)), 2) if working else 0.0,
            "cycle_payload_pct": None,
            "seatbelt_status": "fastened",
            "seat_occupied": True,
            "park_brake": True,
            "gear_state": "park",
            "proximity_distance": None,
            "proximity_sensor_ok": True,
            "ambient_temp": round(float(rng.uniform(*_AMBIENT_TEMP_C)), 2),
            "visibility": round(float(rng.uniform(*_VISIBILITY_M)), 1),
            "tilt_angle": round(
                float(rng.uniform(0.0, machine.tilt_limit_degrees * _TILT_FRACTION_OF_LIMIT)), 3
            ),
        }
        if working:
            self._apply_work_motion(tick, machine, shift, task)

        overrides = scenario_injector.active_overrides(ts, machine.machine_id)
        tick.update(overrides)

        if working and tick["hydraulic_active"]:
            self._advance_cycle(tick, machine, shift, task, payload_overridden="payload_pct" in overrides)
        return tick

    def _apply_work_motion(self, tick: dict, machine: EdgeMachine, shift: EdgeShift, task: EdgeTask) -> None:
        """Working: park brake off. Repositioning: slow travel, implement idle."""
        cycle = self._cycle_for(machine, shift, task)
        tick["park_brake"] = False
        tick["gear_state"] = "neutral"
        if cycle.travel_left > 0:
            cycle.travel_left -= 1
            tick["gear_state"] = "forward"
            tick["machine_speed"] = _REPOSITION_SPEED_KMH
            tick["hydraulic_active"] = False
            tick["payload_pct"] = 0.0

    def _cycle_for(self, machine: EdgeMachine, shift: EdgeShift, task: EdgeTask) -> _CycleState:
        if self._cycle is None or self._cycle.task_id != task.task_id:
            self._cycle = _CycleState(task_id=task.task_id, cycle_seconds=_cycle_seconds_for(machine, shift, task))
        return self._cycle

    def _advance_cycle(
        self, tick: dict, machine: EdgeMachine, shift: EdgeShift, task: EdgeTask, payload_overridden: bool
    ) -> None:
        cycle = self._cycle_for(machine, shift, task)
        cycle.elapsed += 1
        if cycle.elapsed < cycle.cycle_seconds:
            return

        cycle.elapsed = 0
        cycle.completed += 1
        if cycle.completed % _REPOSITION_EVERY_CYCLES == 0:
            cycle.travel_left = _REPOSITION_SECONDS
        # A forced payload (overload scenario) is what the completed cycle carried.
        fill = tick["payload_pct"] if payload_overridden else float(self._rng.uniform(*CYCLE_FILL_PCT_RANGE))
        tick["load_cycles"] = 1
        tick["cycle_payload_pct"] = round(fill, 2)
        tick["payload_pct"] = round(fill, 2)

    async def _active_task(self, session: AsyncSession, shift_id: str) -> EdgeTask | None:
        result = await session.execute(
            select(EdgeTask)
            .where(EdgeTask.shift_id == shift_id)
            .where(EdgeTask.status == TaskStatus.IN_PROGRESS.value)
            .order_by(EdgeTask.actual_start)
            .limit(1)
        )
        return result.scalar_one_or_none()


def _cycle_seconds_for(machine: EdgeMachine, shift: EdgeShift, task: EdgeTask) -> int:
    return cycle_seconds(
        machine_type=MachineType(machine.machine_type),
        task_type=TaskType(task.task_type),
        skill=SkillLevel(task.operator_skill_at_assignment),
        weather_actual=WeatherCategory(shift.weather_actual or shift.weather_forecast),
        material=MaterialType(task.material_type),
        machine_age=machine.machine_age,
        noise=_LIVE_CYCLE_NOISE,
    )
