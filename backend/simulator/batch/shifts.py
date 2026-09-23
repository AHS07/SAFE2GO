"""Shift and task generation.

50 days, one shift per machine per day = 1,000 shifts.
Each shift gets 3-5 tasks. Task types are restricted to those compatible
with the machine type.

The scheduled times and raw_predicted_time written here are a planner's
rule of thumb (marked is_fallback). How long a task really takes is decided
later by the tick stream in telemetry.py.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta

import numpy as np

from app.config.settings import get_settings
from app.shared.compatibility import TASK_UNIT, compatible_task_types
from app.shared.enums import MachineType, MaterialType, QuantityUnit, SkillLevel, TaskType
from simulator.batch.master_data import MachineRecord, OperatorRecord, _rng_uuid
from simulator.batch.weather import WeatherCategory, make_forecast, sample_weather
from simulator.batch.work_model import (
    CYCLE_SECONDS,
    NOMINAL_FILL_FRACTION,
    SKILL_WORK_SHARE,
    TASK_CYCLES_RANGE,
)

_MATERIAL_TYPES: list[MaterialType] = list(MaterialType)

# The planner assumes an average operator: no skill, material or weather effect.
_PLANNING_WORK_SHARE = SKILL_WORK_SHARE[SkillLevel.INTERMEDIATE]


@dataclass
class TaskRecord:
    task_id: str
    shift_id: str
    task_type: TaskType
    operator_skill_at_assignment: SkillLevel
    target_quantity: float
    quantity_unit: QuantityUnit
    material_type: str
    scheduled_start: datetime
    scheduled_end: datetime          # = scheduled_start + planning_eta
    raw_predicted_time: float        # minutes, planner rule of thumb
    planning_eta: float              # raw + buffer
    is_fallback: bool = True         # batch history predates the ETA model
    completed_quantity: float = 0.0
    actual_start: datetime | None = None
    actual_end: datetime | None = None
    paused_minutes: float = 0.0
    blocked_minutes: float = 0.0


@dataclass
class ShiftRecord:
    shift_id: str
    machine_id: str
    operator_id: str
    date: datetime
    scheduled_start: datetime
    scheduled_end: datetime
    weather_forecast: WeatherCategory
    weather_actual: WeatherCategory
    tasks: list[TaskRecord] = field(default_factory=list)


# ---------------------------------------------------------------------------
# Quantities and planning estimate
# ---------------------------------------------------------------------------


def _target_quantity(
    task_type: TaskType,
    bucket_capacity: float,
    rng: np.random.Generator,
) -> tuple[float, QuantityUnit]:
    cycles = float(rng.uniform(*TASK_CYCLES_RANGE))
    unit = TASK_UNIT[task_type]
    if unit == QuantityUnit.LOADS:
        return float(round(cycles)), unit
    return round(cycles * bucket_capacity * NOMINAL_FILL_FRACTION, 1), unit


def estimate_duration(
    quantity: float,
    unit: QuantityUnit,
    machine_type: MachineType,
    bucket_capacity: float,
) -> float:
    """Planner rule of thumb in minutes: cycles needed at nominal pace."""
    if unit == QuantityUnit.LOADS:
        cycles = quantity
    else:
        cycles = quantity / (bucket_capacity * NOMINAL_FILL_FRACTION)
    return round(cycles * CYCLE_SECONDS[machine_type] / 60.0 / _PLANNING_WORK_SHARE, 1)


# ---------------------------------------------------------------------------
# Operator assignment helpers
# ---------------------------------------------------------------------------

def _operator_for_machine(
    machine: MachineRecord,
    operators: list[OperatorRecord],
    rng: np.random.Generator,
) -> OperatorRecord | None:
    qualified = [
        op for op in operators
        if any(mt == machine.machine_type for mt, _ in op.qualifications)
    ]
    if not qualified:
        return None
    return qualified[int(rng.integers(0, len(qualified)))]


def _skill_for_machine(operator: OperatorRecord, machine_type: MachineType) -> SkillLevel:
    for mt, skill in operator.qualifications:
        if mt == machine_type:
            return skill
    return SkillLevel.BEGINNER


# ---------------------------------------------------------------------------
# Main generator
# ---------------------------------------------------------------------------

START_DATE = datetime(2025, 1, 1, tzinfo=UTC)
# Time-ordered split (architecture.md section 9): the first TRAIN_DAYS of
# history train the ETA model and build baselines; the rest is held out.
TRAIN_DAYS = 40
EVALUATION_SPLIT_START = START_DATE + timedelta(days=TRAIN_DAYS)
SHIFT_DURATION_HOURS = 8
N_DAYS = 50


def generate_shifts(
    machines: list[MachineRecord],
    operators: list[OperatorRecord],
    rng: np.random.Generator,
) -> list[ShiftRecord]:
    """Generate 1,000 shifts (20 machines x 50 days)."""
    buffer_minutes = get_settings().eta_buffer_minutes
    n_total = len(machines) * N_DAYS
    actual_weather_list = sample_weather(rng, n_total)

    shifts: list[ShiftRecord] = []
    weather_idx = 0

    for day in range(N_DAYS):
        for machine in machines:
            operator = _operator_for_machine(machine, operators, rng)
            if operator is None:
                weather_idx += 1
                continue

            shift_start_hour = int(rng.integers(6, 10))   # 06:00 - 09:00
            shift_date = START_DATE + timedelta(days=day)
            scheduled_start = shift_date.replace(hour=shift_start_hour, minute=0, second=0)
            scheduled_end = scheduled_start + timedelta(hours=SHIFT_DURATION_HOURS)

            weather_actual = actual_weather_list[weather_idx]
            weather_forecast = make_forecast(weather_actual, rng)
            weather_idx += 1

            shift = ShiftRecord(
                shift_id=_rng_uuid(rng),
                machine_id=machine.machine_id,
                operator_id=operator.operator_id,
                date=shift_date,
                scheduled_start=scheduled_start,
                scheduled_end=scheduled_end,
                weather_forecast=weather_forecast,
                weather_actual=weather_actual,
            )

            task_types_available = compatible_task_types(machine.machine_type)
            n_tasks = int(rng.integers(3, 6))
            skill = _skill_for_machine(operator, machine.machine_type)
            task_cursor = scheduled_start

            for _ in range(n_tasks):
                if task_cursor >= scheduled_end:
                    break

                task_type = task_types_available[int(rng.integers(0, len(task_types_available)))]
                quantity, unit = _target_quantity(task_type, machine.bucket_capacity, rng)
                material = _MATERIAL_TYPES[int(rng.integers(0, len(_MATERIAL_TYPES)))]

                raw_est = estimate_duration(
                    quantity, unit, machine.machine_type, machine.bucket_capacity
                )
                planning_eta = raw_est + buffer_minutes
                task_end = task_cursor + timedelta(minutes=planning_eta)

                shift.tasks.append(
                    TaskRecord(
                        task_id=_rng_uuid(rng),
                        shift_id=shift.shift_id,
                        task_type=task_type,
                        operator_skill_at_assignment=skill,
                        target_quantity=quantity,
                        quantity_unit=unit,
                        material_type=material.value,
                        scheduled_start=task_cursor,
                        scheduled_end=task_end,
                        raw_predicted_time=raw_est,
                        planning_eta=planning_eta,
                    )
                )
                task_cursor = task_end

            shifts.append(shift)

    return shifts
