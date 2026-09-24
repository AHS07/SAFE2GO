"""Tick-by-tick telemetry generator.

One tick per simulation second. A typical 8-hour shift produces 28,800 ticks.

Tasks run in order. A task starts after the previous one ends plus a short
gap, and ends on the tick where its completed quantity reaches the target.
Task duration is therefore an outcome of the tick stream: cycle length and
work share come from the ground-truth work model (work_model.py), driven by
skill, actual weather, material, task type and machine age.

Injected anomaly windows change the raw sensor state only. They never set
alert flags; the safety and behavior engines derive everything.
"""
from __future__ import annotations

from collections.abc import Iterator
from dataclasses import dataclass
from datetime import datetime, timedelta

import numpy as np

from app.config.thresholds import get_thresholds
from app.shared.enums import (
    AnomalyType,
    MachineType,
    MaterialType,
    QuantityUnit,
    SkillLevel,
    TaskType,
)
from simulator.batch.master_data import MachineRecord
from simulator.batch.shifts import ShiftRecord, TaskRecord
from simulator.batch.weather import sample_temperature, sample_visibility
from simulator.batch.work_model import (
    CYCLE_FILL_PCT_RANGE,
    SKILL_WORK_SHARE,
    TASK_NOISE_RANGE,
    TRAVEL_SHARE,
    cycle_seconds,
)

TICK_SECONDS = 1

_IDLE_RPM: dict[MachineType, tuple[float, float]] = {
    MachineType.EXCAVATOR: (600.0, 800.0),
    MachineType.WHEEL_LOADER: (650.0, 850.0),
    MachineType.BACKHOE_LOADER: (600.0, 750.0),
}

# Fraction of rated_max_rpm during active work and normal travel.
_WORK_RPM_FRACTION: dict[MachineType, tuple[float, float]] = {
    MachineType.EXCAVATOR: (0.55, 0.80),
    MachineType.WHEEL_LOADER: (0.60, 0.85),
    MachineType.BACKHOE_LOADER: (0.55, 0.78),
}
_TRAVEL_RPM_FRACTION: dict[MachineType, tuple[float, float]] = {
    MachineType.EXCAVATOR: (0.40, 0.65),
    MachineType.WHEEL_LOADER: (0.55, 0.80),
    MachineType.BACKHOE_LOADER: (0.45, 0.70),
}
_TRAVEL_SPEED: dict[MachineType, tuple[float, float]] = {
    MachineType.EXCAVATOR: (1.0, 5.0),
    MachineType.WHEEL_LOADER: (5.0, 20.0),
    MachineType.BACKHOE_LOADER: (3.0, 15.0),
}

# Fuel consumption rates (L/hour) per state
_FUEL_RATE_IDLE = 3.5
_FUEL_RATE_WORK = 12.0
_FUEL_RATE_TRAVEL = 8.0

# Normal operation
_GAP_SECONDS_RANGE = (60, 180)            # between tasks, engine idling
# A task already under way at shift end is finished in overtime, up to this
# limit. Tasks not started by shift end are left for the next shift.
_MAX_OVERTIME_SECONDS = 3 * 3600
_BLOCKED_CHANCE = 0.15                    # share of tasks with a truck wait
_BLOCKED_SECONDS_RANGE = (120, 480)
_BLOCKED_START_FRACTION = (0.2, 0.6)      # position within the task, by elapsed ticks
_BREAK_CHANCE = 0.5                       # share of shifts with a paused break
_BREAK_SECONDS_RANGE = (600, 1200)
_BREAK_START_FRACTION = (0.4, 0.6)        # position within the shift
_RANDOM_UNBUCKLE_CHANCE = 0.0001
_BACKGROUND_PROXIMITY_CHANCE = 0.005

# Anomaly windows
_OVERLOAD_PCT_RANGE = (108.0, 125.0)
_HIGH_RPM_FRACTION_RANGE = (0.88, 0.97)
_HIGH_RPM_SPEED_FACTOR = (1.1, 1.5)       # multiple of the detector's travel threshold

_IDLE_ANOMALIES = {AnomalyType.EXCESSIVE_IDLING.value, AnomalyType.HIGH_IDLE_RATIO.value}
_TRAVEL_ANOMALIES = {
    AnomalyType.HIGH_RPM_TRAVEL.value,
    AnomalyType.REPEATED_SEATBELT_VIOLATION.value,
}


class _Phase:
    IDLE = "idle"
    WORKING = "working"
    TRAVELLING = "travelling"
    PAUSED = "paused"
    BLOCKED = "blocked"
    ENGINE_OFF = "engine_off"


@dataclass
class _TaskRun:
    task: TaskRecord
    cycle_seconds: int
    work_share: float
    blocked_at_tick: int | None
    blocked_seconds: int
    elapsed_ticks: int = 0
    blocked_left: int = 0
    progress: float = 0.0

    @property
    def done(self) -> bool:
        return self.progress >= self.task.target_quantity


def generate_telemetry(
    shift: ShiftRecord,
    machine: MachineRecord,
    rng: np.random.Generator,
    *,
    anomaly_windows: list[tuple[datetime, datetime, str]] | None = None,
) -> Iterator[dict]:
    """Yield one telemetry dict per tick for the entire shift.

    anomaly_windows: (start, end, anomaly_type) windows from the anomaly
    injector. The generator changes raw sensor state during these windows.
    """
    windows = anomaly_windows or []
    travel_threshold = getattr(
        get_thresholds().behavior.travel_speed_threshold_kmh, MachineType(machine.machine_type).value
    )

    runs = [_plan_task(task, shift, machine, rng) for task in shift.tasks]
    break_window = _plan_break(shift, rng)
    run_idx = 0
    gap_left = 0

    engine_hours = machine.last_service_engine_hours
    fuel_used = 0.0
    total_load_cycles = 0
    cycle_counter = 0
    cycle_fill = float(rng.uniform(*CYCLE_FILL_PCT_RANGE))

    base_vis = sample_visibility(shift.weather_actual, rng)
    base_temp = sample_temperature(shift.weather_actual, rng)

    ts = shift.scheduled_start
    overtime_limit = shift.scheduled_end + timedelta(seconds=_MAX_OVERTIME_SECONDS)
    while ts < shift.scheduled_end or _finishing_in_overtime(runs, run_idx, gap_left, ts, overtime_limit):
        run = runs[run_idx] if run_idx < len(runs) and gap_left == 0 else None
        all_done = run_idx >= len(runs)
        anomaly = _active_anomaly(ts, windows)
        if run is not None:
            _update_blocked(run)
        in_break = break_window is not None and break_window[0] <= ts < break_window[1]
        phase = _determine_phase(run, all_done, anomaly, in_break, rng)

        engine_running = phase not in (_Phase.ENGINE_OFF, _Phase.PAUSED)
        mt = MachineType(machine.machine_type)

        # --- Engine and motion ---
        machine_speed = 0.0
        park_brake = True
        gear_state = "neutral" if engine_running else "park"
        hydraulic_active = False
        if phase == _Phase.WORKING:
            engine_rpm = machine.rated_max_rpm * float(rng.uniform(*_WORK_RPM_FRACTION[mt]))
            gear_state = "park"
            hydraulic_active = True
        elif phase == _Phase.TRAVELLING:
            if anomaly == AnomalyType.HIGH_RPM_TRAVEL.value:
                engine_rpm = machine.rated_max_rpm * float(rng.uniform(*_HIGH_RPM_FRACTION_RANGE))
                machine_speed = travel_threshold * float(rng.uniform(*_HIGH_RPM_SPEED_FACTOR))
            else:
                engine_rpm = machine.rated_max_rpm * float(rng.uniform(*_TRAVEL_RPM_FRACTION[mt]))
                machine_speed = float(rng.uniform(*_TRAVEL_SPEED[mt]))
            park_brake = False
            gear_state = "forward"
        elif engine_running:
            engine_rpm = float(rng.uniform(*_IDLE_RPM[mt]))
        else:
            engine_rpm = 0.0

        # --- Fuel ---
        if phase == _Phase.WORKING:
            fuel_rate = _FUEL_RATE_WORK
        elif phase == _Phase.TRAVELLING:
            fuel_rate = _FUEL_RATE_TRAVEL
        elif engine_running:
            fuel_rate = _FUEL_RATE_IDLE
        else:
            fuel_rate = 0.0
        fuel_used += fuel_rate / 3600.0
        if engine_running:
            engine_hours += 1.0 / 3600.0

        # --- Load cycle and task progress ---
        load_cycles_this_tick = 0
        cycle_payload_pct: float | None = None
        payload_pct = 0.0
        overloading = anomaly == AnomalyType.REPEATED_OVERLOADING.value

        if phase == _Phase.WORKING:
            if run is not None:
                cycle_counter += 1
                payload_pct = (cycle_counter / run.cycle_seconds) * cycle_fill
                if overloading:
                    payload_pct = float(rng.uniform(*_OVERLOAD_PCT_RANGE))
                if cycle_counter >= run.cycle_seconds:
                    load_cycles_this_tick = 1
                    total_load_cycles += 1
                    cycle_payload_pct = round(payload_pct, 2)
                    run.progress += _cycle_quantity(run.task, cycle_payload_pct, machine)
                    cycle_counter = 0
                    cycle_fill = float(rng.uniform(*CYCLE_FILL_PCT_RANGE))
            elif overloading:
                payload_pct = float(rng.uniform(*_OVERLOAD_PCT_RANGE))

        # --- Seatbelt and seat ---
        if anomaly == AnomalyType.REPEATED_SEATBELT_VIOLATION.value or rng.random() < _RANDOM_UNBUCKLE_CHANCE:
            seatbelt = "unfastened"
        else:
            seatbelt = "fastened"

        # --- Proximity (null means nothing in sensor range) ---
        proximity_distance = None
        if rng.random() < _BACKGROUND_PROXIMITY_CHANCE:
            proximity_distance = float(rng.uniform(8.0, 20.0))

        tilt_angle = float(rng.uniform(0.0, machine.tilt_limit_degrees * 0.6))
        visibility = base_vis * float(rng.uniform(0.95, 1.05))
        ambient_temp = base_temp + float(rng.uniform(-1.0, 1.0))

        yield {
            "telemetry_id": None,
            "timestamp": ts,
            "machine_id": machine.machine_id,
            "operator_id": shift.operator_id,
            "task_id": run.task.task_id if run else None,
            "shift_id": shift.shift_id,
            "engine_running": engine_running,
            "engine_rpm": round(engine_rpm, 1),
            "engine_hours": round(engine_hours, 4),
            "fuel_used": round(fuel_used, 4),
            "hydraulic_active": hydraulic_active,
            "machine_speed": round(machine_speed, 2),
            "load_cycles": load_cycles_this_tick,
            "payload_pct": round(payload_pct, 2),
            "cycle_payload_pct": cycle_payload_pct,
            "seatbelt_status": seatbelt,
            "seat_occupied": True,
            "park_brake": park_brake,
            "gear_state": gear_state,
            "proximity_distance": round(proximity_distance, 2) if proximity_distance is not None else None,
            "proximity_sensor_ok": True,
            "ambient_temp": round(ambient_temp, 2),
            "visibility": round(visibility, 1),
            "tilt_angle": round(tilt_angle, 3),
            # Internal bookkeeping, not stored in the DB
            "_phase": phase,
            "_total_load_cycles": total_load_cycles,
        }

        # --- Advance task state ---
        if run is not None:
            run.elapsed_ticks += 1
            if run.done:
                run_idx += 1
                gap_left = int(rng.integers(*_GAP_SECONDS_RANGE))
                cycle_counter = 0
        elif gap_left > 0:
            gap_left -= 1

        ts = ts + timedelta(seconds=TICK_SECONDS)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _finishing_in_overtime(
    runs: list[_TaskRun], run_idx: int, gap_left: int, ts: datetime, overtime_limit: datetime
) -> bool:
    """True while a task that started before shift end is still unfinished."""
    if ts >= overtime_limit or gap_left > 0 or run_idx >= len(runs):
        return False
    run = runs[run_idx]
    return run.elapsed_ticks > 0 and not run.done


def _plan_task(
    task: TaskRecord,
    shift: ShiftRecord,
    machine: MachineRecord,
    rng: np.random.Generator,
) -> _TaskRun:
    skill = SkillLevel(task.operator_skill_at_assignment)
    seconds = cycle_seconds(
        MachineType(machine.machine_type),
        TaskType(task.task_type),
        skill,
        shift.weather_actual,
        MaterialType(task.material_type),
        machine.machine_age,
        float(rng.uniform(*TASK_NOISE_RANGE)),
    )
    blocked_at: int | None = None
    blocked_seconds = 0
    if rng.random() < _BLOCKED_CHANCE:
        expected_ticks = task.raw_predicted_time * 60.0
        blocked_at = int(expected_ticks * float(rng.uniform(*_BLOCKED_START_FRACTION)))
        blocked_seconds = int(rng.integers(*_BLOCKED_SECONDS_RANGE))
    return _TaskRun(
        task=task,
        cycle_seconds=seconds,
        work_share=SKILL_WORK_SHARE[skill],
        blocked_at_tick=blocked_at,
        blocked_seconds=blocked_seconds,
    )


def _plan_break(
    shift: ShiftRecord,
    rng: np.random.Generator,
) -> tuple[datetime, datetime] | None:
    if rng.random() >= _BREAK_CHANCE:
        return None
    shift_seconds = (shift.scheduled_end - shift.scheduled_start).total_seconds()
    start = shift.scheduled_start + timedelta(
        seconds=shift_seconds * float(rng.uniform(*_BREAK_START_FRACTION))
    )
    return start, start + timedelta(seconds=int(rng.integers(*_BREAK_SECONDS_RANGE)))


def _update_blocked(run: _TaskRun) -> None:
    """Start the planned truck wait once the task reaches its tick offset."""
    if run.blocked_left > 0:
        run.blocked_left -= 1
    elif run.blocked_at_tick is not None and run.elapsed_ticks >= run.blocked_at_tick:
        run.blocked_left = run.blocked_seconds
        run.blocked_at_tick = None


def _determine_phase(
    run: _TaskRun | None,
    all_done: bool,
    anomaly: str | None,
    in_break: bool,
    rng: np.random.Generator,
) -> str:
    if anomaly in _IDLE_ANOMALIES:
        return _Phase.IDLE
    if anomaly == AnomalyType.REPEATED_OVERLOADING.value:
        return _Phase.WORKING
    if anomaly in _TRAVEL_ANOMALIES:
        return _Phase.TRAVELLING

    if run is None:
        # Short gaps between tasks idle; once all tasks are done the engine is off.
        return _Phase.ENGINE_OFF if all_done else _Phase.IDLE

    if anomaly == AnomalyType.LEGIT_WAIT.value or run.blocked_left > 0:
        return _Phase.BLOCKED
    if in_break:
        return _Phase.PAUSED   # engine off during the break

    roll = rng.random()
    if roll < run.work_share:
        return _Phase.WORKING
    if roll < run.work_share + TRAVEL_SHARE:
        return _Phase.TRAVELLING
    return _Phase.IDLE


def _cycle_quantity(task: TaskRecord, cycle_payload_pct: float, machine: MachineRecord) -> float:
    """Quantity moved by one completed cycle, matching summaries.derive_actuals."""
    if task.quantity_unit == QuantityUnit.LOADS:
        return 1.0
    return (cycle_payload_pct / 100.0) * machine.bucket_capacity


def _active_anomaly(
    ts: datetime,
    windows: list[tuple[datetime, datetime, str]],
) -> str | None:
    for start, end, atype in windows:
        if start <= ts < end:
            return atype
    return None
