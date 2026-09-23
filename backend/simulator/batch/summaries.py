"""Derive shift summaries and task actuals from generated telemetry.

Processes the tick stream for a shift and computes:
  - completed_quantity per task (from cycle-completion ticks)
  - actual_start, actual_end, paused_minutes, blocked_minutes per task
  - shift-level summary: engine_hours, idle_minutes, idle_ratio,
    cycle_count, completed_quantity, fuel_used, cycle_rate
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta

from simulator.batch.shifts import ShiftRecord, TaskRecord
from simulator.batch.telemetry import TICK_SECONDS, _Phase


@dataclass
class ShiftSummaryData:
    shift_id: str
    engine_hours: float
    idle_minutes: float
    idle_ratio: float
    cycle_count: int
    completed_quantity: float
    fuel_used: float
    cycle_rate: float   # cycles per engine hour


def derive_actuals(
    shift: ShiftRecord,
    ticks: list[dict],
) -> tuple[list[TaskRecord], ShiftSummaryData]:
    """Process tick list and update task actuals and shift summary in place.

    Returns updated task list and shift summary.
    """
    # --- Per-task tracking ---
    task_map: dict[str, TaskRecord] = {t.task_id: t for t in shift.tasks}
    task_first_tick: dict[str, datetime] = {}
    task_last_tick: dict[str, datetime] = {}
    task_paused_secs: dict[str, float] = {t.task_id: 0.0 for t in shift.tasks}
    task_blocked_secs: dict[str, float] = {t.task_id: 0.0 for t in shift.tasks}

    # completed_quantity: sum over cycle-completion ticks
    task_completed_quantity: dict[str, float] = {t.task_id: 0.0 for t in shift.tasks}

    # --- Shift-level counters ---
    total_engine_seconds = 0.0
    total_idle_seconds = 0.0
    total_cycle_count = 0
    total_fuel = 0.0
    shift_completed_quantity = 0.0

    prev_fuel = 0.0

    for tick in ticks:
        ts: datetime = tick["timestamp"]
        task_id: str | None = tick["task_id"]
        phase: str = tick["_phase"]

        # Fuel delta
        tick_fuel = tick["fuel_used"] - prev_fuel
        prev_fuel = tick["fuel_used"]
        total_fuel += tick_fuel

        if tick["engine_running"]:
            total_engine_seconds += 1.0

        if phase == _Phase.IDLE:
            total_idle_seconds += 1.0

        if task_id and task_id in task_map:
            if task_id not in task_first_tick:
                task_first_tick[task_id] = ts
            task_last_tick[task_id] = ts

            if phase == _Phase.PAUSED:
                task_paused_secs[task_id] += 1.0
            elif phase == _Phase.BLOCKED:
                task_blocked_secs[task_id] += 1.0

            # Completed quantity from cycle-completion ticks only
            if tick["load_cycles"] > 0 and tick["cycle_payload_pct"] is not None:
                task = task_map[task_id]
                machine_bucket = _get_bucket_capacity(tick)
                from app.shared.enums import QuantityUnit
                if task.quantity_unit == QuantityUnit.M3:
                    qty = (tick["cycle_payload_pct"] / 100.0) * machine_bucket
                else:
                    qty = 1.0  # one bucket load
                task_completed_quantity[task_id] += qty
                shift_completed_quantity += qty

        total_cycle_count += tick["load_cycles"]

    # --- Update task records ---
    for task in shift.tasks:
        tid = task.task_id
        if tid in task_first_tick:
            task.actual_start = task_first_tick[tid]
            # A tick covers one second, so the task ends when its last tick ends.
            task.actual_end = task_last_tick[tid] + timedelta(seconds=TICK_SECONDS)
            task.paused_minutes = round(task_paused_secs[tid] / 60.0, 2)
            task.blocked_minutes = round(task_blocked_secs[tid] / 60.0, 2)
            task.completed_quantity = round(task_completed_quantity[tid], 3)

    # --- Shift summary ---
    engine_hours = total_engine_seconds / 3600.0
    idle_minutes = total_idle_seconds / 60.0
    idle_ratio = (total_idle_seconds / total_engine_seconds) if total_engine_seconds > 0 else 0.0
    cycle_rate = (total_cycle_count / engine_hours) if engine_hours > 0 else 0.0

    summary = ShiftSummaryData(
        shift_id=shift.shift_id,
        engine_hours=round(engine_hours, 3),
        idle_minutes=round(idle_minutes, 2),
        idle_ratio=round(idle_ratio, 4),
        cycle_count=total_cycle_count,
        completed_quantity=round(shift_completed_quantity, 3),
        fuel_used=round(total_fuel, 3),
        cycle_rate=round(cycle_rate, 3),
    )

    return list(task_map.values()), summary


def _get_bucket_capacity(tick: dict) -> float:
    """Retrieve bucket capacity from tick metadata (set by generator)."""
    return tick.get("_bucket_capacity", 1.0)
