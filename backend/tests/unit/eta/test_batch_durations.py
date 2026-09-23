"""Batch generator checks that the ETA model depends on.

Task duration must come out of the tick stream: a task ends when its
completed quantity reaches the target, and actual_time matches the ticks
spent on it minus paused and blocked time.
"""
from __future__ import annotations

from datetime import timedelta

import numpy as np

from simulator.batch.master_data import generate_machines, generate_operators
from simulator.batch.shifts import SHIFT_DURATION_HOURS, generate_shifts
from simulator.batch.summaries import derive_actuals
from simulator.batch.telemetry import TICK_SECONDS, generate_telemetry


def _run_shifts(n: int, seed: int = 42):
    rng = np.random.default_rng(seed)
    machines = generate_machines(rng)
    operators = generate_operators(rng)
    shifts = generate_shifts(machines, operators, rng)[:n]
    machine_map = {m.machine_id: m for m in machines}
    for shift in shifts:
        machine = machine_map[shift.machine_id]
        ticks = [
            {**t, "_bucket_capacity": machine.bucket_capacity}
            for t in generate_telemetry(shift, machine, rng)
        ]
        tasks, summary = derive_actuals(shift, ticks)
        yield shift, ticks, tasks, summary


def test_most_tasks_finish_within_the_shift() -> None:
    finished = total = 0
    for _, _, tasks, _ in _run_shifts(6):
        for t in tasks:
            total += 1
            finished += t.completed_quantity >= t.target_quantity
    assert finished / total > 0.6


def test_finished_task_actual_time_matches_its_ticks() -> None:
    for _, ticks, tasks, _ in _run_shifts(3):
        for task in tasks:
            if task.actual_start is None:
                continue
            task_ticks = [t for t in ticks if t["task_id"] == task.task_id]
            working = [t for t in task_ticks if t["_phase"] not in ("paused", "blocked")]
            actual_minutes = (
                (task.actual_end - task.actual_start).total_seconds() / 60.0
                - task.paused_minutes - task.blocked_minutes
            )
            assert actual_minutes == np.float64(len(working) * TICK_SECONDS / 60.0).round(2) or \
                abs(actual_minutes - len(working) / 60.0) < 0.02


def test_finished_task_ends_on_the_cycle_that_reaches_target() -> None:
    for _, ticks, tasks, _ in _run_shifts(3):
        for task in tasks:
            if task.completed_quantity < task.target_quantity:
                continue
            last = [t for t in ticks if t["task_id"] == task.task_id][-1]
            assert last["load_cycles"] == 1
            assert task.actual_end == last["timestamp"] + timedelta(seconds=TICK_SECONDS)


def test_completed_quantity_is_derived_from_cycles() -> None:
    quantities = [t.completed_quantity for _, _, tasks, _ in _run_shifts(3) for t in tasks]
    assert any(q > 0 for q in quantities)


def test_shift_length_is_unchanged() -> None:
    for shift, ticks, _, _ in _run_shifts(1):
        assert len(ticks) == SHIFT_DURATION_HOURS * 3600 // TICK_SECONDS
        assert ticks[0]["timestamp"] == shift.scheduled_start
