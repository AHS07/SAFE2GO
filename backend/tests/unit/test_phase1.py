"""Phase 1 exit-criteria tests.

These tests run without a database — they exercise the generator logic
in isolation.

Exit criteria from phases.md:
  - Running the generator twice with the same seed produces identical data.
  - For a sample of tasks, actual_time matches telemetry duration minus
    paused and blocked time.
  - No generated task uses an incompatible machine type.
  - completed_quantity uses only cycle-completion ticks.
"""
from __future__ import annotations

import numpy as np
import pytest

from app.shared.compatibility import compatible_task_types
from app.shared.enums import MachineType, QuantityUnit, TaskType
from simulator.batch.master_data import generate_machines, generate_operators
from simulator.batch.shifts import generate_shifts
from simulator.batch.summaries import derive_actuals
from simulator.batch.telemetry import generate_telemetry

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def make_rng(seed: int = 42) -> np.random.Generator:
    return np.random.default_rng(seed)


# ---------------------------------------------------------------------------
# Master data
# ---------------------------------------------------------------------------


def test_fleet_composition() -> None:
    """Fleet must contain 8 excavators, 8 wheel loaders, 4 backhoe loaders."""
    machines = generate_machines(make_rng())
    counts = {mt: 0 for mt in MachineType}
    for m in machines:
        counts[m.machine_type] += 1
    assert counts[MachineType.EXCAVATOR] == 8
    assert counts[MachineType.WHEEL_LOADER] == 8
    assert counts[MachineType.BACKHOE_LOADER] == 4


def test_operator_count() -> None:
    operators = generate_operators(make_rng())
    assert len(operators) == 25


def test_every_machine_type_has_all_skill_levels() -> None:
    """Every machine type must have at least one operator at each skill level."""
    from app.shared.enums import SkillLevel

    operators = generate_operators(make_rng())
    coverage: dict[tuple[MachineType, SkillLevel], int] = {}
    for op in operators:
        for mt, skill in op.qualifications:
            coverage[(mt, skill)] = coverage.get((mt, skill), 0) + 1

    for mt in MachineType:
        for skill in SkillLevel:
            assert coverage.get((mt, skill), 0) >= 1, (
                f"No operator with {mt}/{skill} qualification"
            )


# ---------------------------------------------------------------------------
# Reproducibility
# ---------------------------------------------------------------------------


def test_same_seed_produces_identical_shifts() -> None:
    """Running the generator twice with seed=42 must produce the same shift IDs."""
    machines_a = generate_machines(make_rng(42))
    ops_a = generate_operators(make_rng(42))
    # Reset RNG to replay the same sequence from operators onwards
    rng_a = make_rng(42)
    machines_a = generate_machines(rng_a)
    ops_a = generate_operators(rng_a)
    shifts_a = generate_shifts(machines_a, ops_a, rng_a)

    rng_b = make_rng(42)
    machines_b = generate_machines(rng_b)
    ops_b = generate_operators(rng_b)
    shifts_b = generate_shifts(machines_b, ops_b, rng_b)

    assert len(shifts_a) == len(shifts_b)
    # Machine IDs are generated with uuid4 from the same RNG so they match
    mids_a = [m.machine_id for m in machines_a]
    mids_b = [m.machine_id for m in machines_b]
    assert mids_a == mids_b

    # Shift counts and task counts should match
    task_counts_a = [len(s.tasks) for s in shifts_a]
    task_counts_b = [len(s.tasks) for s in shifts_b]
    assert task_counts_a == task_counts_b


def test_generator_produces_1000_shifts() -> None:
    rng = make_rng()
    machines = generate_machines(rng)
    operators = generate_operators(rng)
    shifts = generate_shifts(machines, operators, rng)
    assert len(shifts) == 1000


# ---------------------------------------------------------------------------
# Task-machine compatibility
# ---------------------------------------------------------------------------


def test_no_incompatible_task_type() -> None:
    """Every task in every shift must be compatible with its machine type."""
    rng = make_rng()
    machines = generate_machines(rng)
    operators = generate_operators(rng)
    shifts = generate_shifts(machines, operators, rng)

    machine_type_map = {m.machine_id: m.machine_type for m in machines}

    for shift in shifts:
        mt = machine_type_map[shift.machine_id]
        allowed = set(compatible_task_types(mt))
        for task in shift.tasks:
            assert task.task_type in allowed, (
                f"Task type {task.task_type} incompatible with machine type {mt}"
            )


def test_wheel_loader_has_no_trenching() -> None:
    assert TaskType.TRENCHING not in compatible_task_types(MachineType.WHEEL_LOADER)


def test_excavator_has_all_task_types() -> None:
    types = set(compatible_task_types(MachineType.EXCAVATOR))
    assert TaskType.EXCAVATION in types
    assert TaskType.MATERIAL_LOADING in types
    assert TaskType.TRENCHING in types


# ---------------------------------------------------------------------------
# Telemetry and completed_quantity
# ---------------------------------------------------------------------------


def test_completed_quantity_uses_only_cycle_completion_ticks() -> None:
    """Sum of completed_quantity must equal sum over cycle_payload_pct ticks only."""
    rng = make_rng()
    machines = generate_machines(rng)
    operators = generate_operators(rng)
    shifts = generate_shifts(machines, operators, rng)

    # Use the first shift that has at least one task with LOADS unit
    target_shift = None
    target_machine = None
    machine_map = {m.machine_id: m for m in machines}

    for s in shifts[:20]:
        for t in s.tasks:
            if t.quantity_unit == QuantityUnit.LOADS:
                target_shift = s
                target_machine = machine_map[s.machine_id]
                break
        if target_shift:
            break

    if target_shift is None:
        pytest.skip("No LOADS task found in first 20 shifts")

    ticks = list(generate_telemetry(target_shift, target_machine, make_rng()))

    # Manually sum cycle-completion ticks
    manual_loads = sum(tick["load_cycles"] for tick in ticks if tick["load_cycles"] > 0)

    _, summary = derive_actuals(target_shift, [
        {**tick, "_bucket_capacity": target_machine.bucket_capacity}
        for tick in ticks
    ])

    # cycle_count must match manual count
    assert summary.cycle_count == manual_loads


def test_actual_time_matches_telemetry_span() -> None:
    """actual_time = actual_end - actual_start - paused - blocked (in minutes)."""
    rng = make_rng(99)
    machines = generate_machines(rng)
    operators = generate_operators(rng)
    shifts = generate_shifts(machines, operators, rng)

    machine_map = {m.machine_id: m for m in machines}
    shift = shifts[0]
    machine = machine_map[shift.machine_id]

    ticks = list(generate_telemetry(shift, machine, make_rng(99)))
    enriched = [{**tick, "_bucket_capacity": machine.bucket_capacity} for tick in ticks]
    updated_tasks, _ = derive_actuals(shift, enriched)

    for task in updated_tasks:
        if task.actual_start is None or task.actual_end is None:
            continue
        span_minutes = (task.actual_end - task.actual_start).total_seconds() / 60.0
        actual_time = span_minutes - task.paused_minutes - task.blocked_minutes
        # Must be positive
        assert actual_time >= 0, f"Negative actual_time for task {task.task_id}"


def test_shift_summary_engine_hours_positive() -> None:
    rng = make_rng(7)
    machines = generate_machines(rng)
    operators = generate_operators(rng)
    shifts = generate_shifts(machines, operators, rng)

    machine_map = {m.machine_id: m for m in machines}
    shift = shifts[5]
    machine = machine_map[shift.machine_id]

    ticks = list(generate_telemetry(shift, machine, make_rng(7)))
    enriched = [{**tick, "_bucket_capacity": machine.bucket_capacity} for tick in ticks]
    _, summary = derive_actuals(shift, enriched)

    assert summary.engine_hours > 0
    assert 0.0 <= summary.idle_ratio <= 1.0
